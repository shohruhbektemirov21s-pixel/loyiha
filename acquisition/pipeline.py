"""AcquisitionPipeline — top-level hardware-to-API bridge.

Responsibilities
----------------
1. Drive the watchdog/driver loop to collect ScanBundles.
2. Feed each bundle through IngestPipeline to produce an AcquisitionResult
   (validates frames, encrypts+stores bytes, assigns scan_id).
3. POST the AcquisitionResult to POST /v1/detect on the serving API.
4. Enforce idempotency: if an identical frame SHA-256 was already posted in
   this session, skip the re-post (protects against double-ingest on reconnect).
5. Surface fidelity metadata as structured log fields so ops can verify
   which scanner path was active for each scan.

Error discipline
----------------
- IngestValidationError (bad frame bytes/dimensions): log + continue. Never
  crash the loop on a single bad scan.
- ScannerUnavailableError (reconnect budget exhausted): log CRITICAL and
  re-raise — the process supervisor (systemd, Docker restart policy) should
  restart the service.
- httpx network error posting to /v1/detect: retry up to ``api_retries``
  times with a short backoff, then log ERROR and continue (don't lose the
  scan loop because the API is momentarily down).

Async design
------------
The driver's read_scan() is blocking; it runs in asyncio.run_in_executor
so the event loop stays free for the HTTP POST and any future health-check
endpoint the pipeline may expose.

The pipeline is designed to run as a standalone process (``python -m
acquisition.pipeline``) or embedded in the same process as the API server
for single-box deployments.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import httpx

from acquisition.protocol import (
    CaptureConfig,
    CaptureMetadata,
    ScanBundle,
    ScannerUnavailableError,
)
from acquisition.watchdog import ConnectionWatchdog
from contracts.v1 import AcquisitionResult, ImageModality
from datalayer.ingestion import IngestConfig, IngestPipeline, IngestValidationError

log = logging.getLogger("xray.acquisition.pipeline")

# How long to wait between API retry attempts (seconds).
_RETRY_BACKOFF: tuple[float, ...] = (1.0, 3.0, 8.0)


@dataclass
class PipelineStats:
    """Counters exposed for ops/monitoring."""
    scans_ingested:  int = 0
    scans_posted:    int = 0
    scans_failed:    int = 0
    api_retries:     int = 0
    started_at:      float = field(default_factory=time.time)


class AcquisitionPipeline:
    """Wires watchdog → IngestPipeline → POST /v1/detect in an async loop.

    Instantiate via ``build_acquisition_pipeline()`` in composition.py.
    """

    def __init__(
        self,
        watchdog: ConnectionWatchdog,
        ingest: IngestPipeline,
        cfg: CaptureConfig,
        *,
        api_retries: int = 3,
    ) -> None:
        self._dog    = watchdog
        self._ingest = ingest
        self._cfg    = cfg
        self._api_retries = api_retries
        self._stats  = PipelineStats()
        # Per-session dedup: set of frame SHA-256 hashes already posted.
        self._seen_hashes: set[str] = set()
        self._stop_event = asyncio.Event()

    @property
    def stats(self) -> PipelineStats:
        return self._stats

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    async def run_forever(self) -> None:
        """Run the acquisition loop until stopped or scanner becomes unavailable."""
        log.info(
            "AcquisitionPipeline starting: scanner=%s lane=%s driver=%s api=%s",
            self._cfg.scanner_id, self._cfg.lane_id,
            self._cfg.driver_type.value, self._cfg.api_base_url,
        )

        loop = asyncio.get_running_loop()
        self._install_signal_handlers()

        try:
            while not self._stop_event.is_set():
                await self._run_one_cycle(loop)
        except ScannerUnavailableError as exc:
            log.critical(
                "AcquisitionPipeline: scanner unavailable — stopping. "
                "Restart this process when hardware is back online. Error: %s", exc,
            )
            raise
        finally:
            self._dog.disconnect()
            log.info(
                "AcquisitionPipeline stopped. stats=%s",
                vars(self._stats),
            )

    def stop(self) -> None:
        """Signal the loop to exit cleanly after the current scan completes."""
        self._stop_event.set()

    # ------------------------------------------------------------------
    # One read→ingest→post cycle
    # ------------------------------------------------------------------
    async def _run_one_cycle(self, loop: asyncio.AbstractEventLoop) -> None:
        # 1. Blocking read off scanner (in executor so event loop isn't blocked).
        try:
            bundle: ScanBundle = await loop.run_in_executor(
                None, self._dog.monitored_read
            )
        except ScannerUnavailableError:
            raise   # let run_forever handle it
        except Exception as exc:
            log.error("Unexpected error from watchdog: %s", exc, exc_info=True)
            await asyncio.sleep(1.0)
            return

        # 2. Dedup check — skip frames already posted in this session.
        hashes = [
            hashlib.sha256(f.raw_bytes).hexdigest()
            for f in bundle.frames
        ]
        combined = hashlib.sha256("|".join(hashes).encode()).hexdigest()
        if combined in self._seen_hashes:
            log.info("AcquisitionPipeline: skipping duplicate scan (hash=%s...)", combined[:12])
            return
        self._seen_hashes.add(combined)

        # 3. Log fidelity for ops.
        self._log_fidelity(bundle)

        # 4. Ingest: validate + encrypt-store → AcquisitionResult.
        try:
            result: AcquisitionResult = await loop.run_in_executor(
                None,
                lambda: self._ingest.ingest(
                    bundle.frames,
                    captured_at=datetime.now(timezone.utc),
                    notes=self._fidelity_note(bundle),
                ),
            )
        except IngestValidationError as exc:
            self._stats.scans_failed += 1
            log.error("AcquisitionPipeline: ingest validation failed: %s", exc)
            return

        self._stats.scans_ingested += 1

        # 5. POST to /v1/detect.
        await self._post_with_retry(result)

    # ------------------------------------------------------------------
    # HTTP POST to /v1/detect
    # ------------------------------------------------------------------
    async def _post_with_retry(self, result: AcquisitionResult) -> None:
        url = f"{self._cfg.api_base_url.rstrip('/')}/v1/detect"
        payload = result.model_dump(mode="json")

        for attempt, backoff in enumerate([0.0] + list(_RETRY_BACKOFF[: self._api_retries - 1])):
            if backoff:
                await asyncio.sleep(backoff)
                self._stats.api_retries += 1
            try:
                async with httpx.AsyncClient(timeout=self._cfg.api_timeout_s) as client:
                    resp = await client.post(url, json=payload)
                if resp.status_code in (200, 201, 202):
                    self._stats.scans_posted += 1
                    log.info(
                        "AcquisitionPipeline: posted scan_id=%s → %d",
                        result.scan_id, resp.status_code,
                    )
                    return
                elif resp.status_code == 409:
                    # Idempotent duplicate — the API already has this scan.
                    log.info(
                        "AcquisitionPipeline: scan_id=%s already exists (409), skipping.",
                        result.scan_id,
                    )
                    return
                else:
                    log.warning(
                        "AcquisitionPipeline: POST %s returned %d (attempt %d/%d): %s",
                        url, resp.status_code, attempt + 1, self._api_retries, resp.text[:200],
                    )
            except httpx.TransportError as exc:
                log.warning(
                    "AcquisitionPipeline: transport error posting scan_id=%s "
                    "(attempt %d/%d): %s",
                    result.scan_id, attempt + 1, self._api_retries, exc,
                )

        self._stats.scans_failed += 1
        log.error(
            "AcquisitionPipeline: failed to post scan_id=%s after %d attempts.",
            result.scan_id, self._api_retries,
        )

    # ------------------------------------------------------------------
    # Fidelity helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _log_fidelity(bundle: ScanBundle) -> None:
        for i, meta in enumerate(bundle.metadata):
            if not meta.is_raw_dual_energy and meta.fidelity_note:
                log.warning(
                    "Fidelity warning frame[%d]: driver=%s is_raw_dual_energy=False — %s",
                    i, meta.driver_type.value, meta.fidelity_note,
                )
            else:
                log.info(
                    "Fidelity frame[%d]: driver=%s is_raw_dual_energy=%s bits=%d model=%s",
                    i, meta.driver_type.value, meta.is_raw_dual_energy,
                    meta.pixel_depth_bits, meta.scanner_model,
                )

    @staticmethod
    def _fidelity_note(bundle: ScanBundle) -> str | None:
        notes = [
            m.fidelity_note for m in bundle.metadata if m.fidelity_note
        ]
        return " | ".join(notes) if notes else None

    # ------------------------------------------------------------------
    # Signal handling (SIGTERM/SIGINT → clean stop)
    # ------------------------------------------------------------------
    def _install_signal_handlers(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self.stop)
        except (NotImplementedError, RuntimeError):
            pass   # Windows or non-main-thread: ignore


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------
def _main() -> None:
    import argparse
    from acquisition.composition import build_acquisition_pipeline

    parser = argparse.ArgumentParser(description="X-ray acquisition pipeline")
    parser.add_argument("--scanner-id", default=None)
    parser.add_argument("--lane-id", default=None)
    parser.add_argument("--driver", default=None, choices=["dicos", "vendor_sdk", "framegrab"])
    args = parser.parse_args()

    pipeline = build_acquisition_pipeline(
        scanner_id_override=args.scanner_id,
        lane_id_override=args.lane_id,
        driver_override=args.driver,
    )
    asyncio.run(pipeline.run_forever())


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _main()


__all__ = ["AcquisitionPipeline", "PipelineStats"]
