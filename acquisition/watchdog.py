"""ConnectionWatchdog — resilient reconnect loop for scanner drivers.

Wraps any ScannerDriver and makes it self-healing:

  - Frame timeout: if ``read_scan`` hangs beyond ``frame_timeout_s``, the
    watchdog abandons the read (a reusable single-worker executor; the hung
    worker is rotated out on the next read) and reconnects.
  - Connection drops: if ``read_scan`` raises ScannerConnectionError,
    watchdog reconnects with exponential backoff.
  - Malformed frames: ScannerFrameError is logged and counted; after
    ``max_consecutive_errors`` in a row the watchdog reconnects.
  - Reconnect budget: after ``max_reconnect_attempts`` the watchdog raises
    ScannerUnavailableError — the pipeline must surface this to ops.

Design notes:
  - read_scan() is run on the watchdog's own single-worker executor so a hung
    driver cannot block the caller past the hard timeout. The worker is reused
    across reads (no per-read thread churn); a genuinely hung worker is rotated
    out on the next read and the executor is released on disconnect().
  - Reconnect itself is synchronous and inline with the calling thread.
    If you need non-blocking reconnects, run the pipeline in a dedicated
    thread or asyncio executor.
  - exponential_backoff: delay = min(base_delay * 2^attempt, max_delay_s)
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from acquisition.protocol import (
    CaptureConfig,
    ScanBundle,
    ScannerConnectionError,
    ScannerDriver,
    ScannerFrameError,
    ScannerTimeoutError,
    ScannerUnavailableError,
)

log = logging.getLogger("xray.acquisition.watchdog")

_MAX_BACKOFF_S: float = 120.0   # cap exponential backoff at 2 minutes


@dataclass
class WatchdogStats:
    """Counters surfaced to ops/monitoring."""
    total_scans:         int = 0
    total_errors:        int = 0
    total_reconnects:    int = 0
    consecutive_errors:  int = 0
    last_error:          str = ""
    last_reconnect_at:   float = 0.0


class ConnectionWatchdog:
    """Wraps a ScannerDriver with reconnect-on-failure semantics.

    Usage::

        driver = DICOSDriver(cfg)
        dog = ConnectionWatchdog(driver, cfg)
        dog.connect()
        while True:
            bundle = dog.monitored_read()   # blocks; auto-heals on errors
            ...
    """

    # After this many consecutive frame errors without a successful read,
    # the watchdog forces a reconnect even if the transport looks healthy.
    MAX_CONSECUTIVE_FRAME_ERRORS: int = 5

    def __init__(self, driver: ScannerDriver, cfg: CaptureConfig) -> None:
        self._driver = driver
        self._cfg = cfg
        self._stats = WatchdogStats()
        # A single reusable worker thread for the timed read (O'RTA-10). The old
        # implementation spawned a fresh daemon thread on EVERY read; under a
        # hang those accumulated without bound. Now the healthy path reuses one
        # thread (zero churn). On a hang the worker is genuinely stuck inside the
        # driver, so we abandon that one executor (its thread can only exit when
        # the driver returns) and spin up a fresh one for the next read — the
        # leak is bounded to one thread per *distinct* hang, not one per read.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="acq-read"
        )
        self._pending: Future | None = None

    @property
    def stats(self) -> WatchdogStats:
        return self._stats

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def connect(self) -> None:
        """Connect with the configured retry budget."""
        self._reconnect(initial=True)

    def disconnect(self) -> None:
        try:
            self._driver.disconnect()
        except Exception as exc:
            log.warning("Watchdog: disconnect error (ignored): %s", exc)
        finally:
            # Release the read worker. Don't wait on a possibly-hung read; a
            # hung worker is a daemon-style abandon, the executor itself is
            # released so its bookkeeping doesn't linger.
            self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def monitored_read(self) -> ScanBundle:
        """Call driver.read_scan() with timeout enforcement and auto-reconnect.

        Returns a ScanBundle on success.
        Raises ScannerUnavailableError only when the reconnect budget is
        exhausted — all other errors are healed internally.
        """
        timeout_s = self._cfg.frame_timeout_s

        while True:
            if not self._driver.is_connected:
                self._reconnect()

            result = self._timed_read(timeout_s)

            if isinstance(result, ScanBundle):
                self._stats.total_scans += 1
                self._stats.consecutive_errors = 0
                return result

            # result is an exception instance
            exc = result
            self._stats.total_errors += 1
            self._stats.consecutive_errors += 1
            self._stats.last_error = str(exc)

            if isinstance(exc, ScannerTimeoutError):
                log.warning(
                    "Watchdog: frame timeout after %.0fs (scanner=%s). Reconnecting.",
                    timeout_s, self._cfg.scanner_id,
                )
                self._reconnect()

            elif isinstance(exc, ScannerConnectionError):
                log.error(
                    "Watchdog: connection error on scanner=%s: %s. Reconnecting.",
                    self._cfg.scanner_id, exc,
                )
                self._reconnect()

            elif isinstance(exc, ScannerFrameError):
                log.warning(
                    "Watchdog: malformed frame from scanner=%s: %s "
                    "(consecutive=%d/%d).",
                    self._cfg.scanner_id, exc,
                    self._stats.consecutive_errors,
                    self.MAX_CONSECUTIVE_FRAME_ERRORS,
                )
                if self._stats.consecutive_errors >= self.MAX_CONSECUTIVE_FRAME_ERRORS:
                    log.error(
                        "Watchdog: %d consecutive frame errors — forcing reconnect.",
                        self._stats.consecutive_errors,
                    )
                    self._reconnect()
                # else: continue loop, try next read

            else:
                # Unexpected exception type — re-raise immediately.
                raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _timed_read(self, timeout_s: float) -> ScanBundle | Exception:
        """Run driver.read_scan() on the reusable worker with a hard timeout.

        Returns a ScanBundle on success or the caught exception on failure.

        Reuses one worker thread across reads (no per-read thread spawn). If a
        previous read hung, its Future is still outstanding on the single-worker
        executor; we detect that and rotate to a fresh executor so this read is
        not blocked behind the stuck one. The stuck thread is abandoned — it can
        only exit when the driver finally returns — but exactly one thread leaks
        per distinct hang, never one per read.
        """
        # If the prior read is still running (hung), don't queue behind it on the
        # single worker — rotate the executor so the old worker is abandoned and
        # a fresh one serves this read.
        if self._pending is not None and not self._pending.done():
            log.warning(
                "Watchdog: previous read still running — abandoning its worker "
                "and rotating the read executor (scanner=%s).",
                self._cfg.scanner_id,
            )
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="acq-read"
            )
        self._pending = None

        future = self._executor.submit(self._driver.read_scan, timeout_s=timeout_s)
        # Wait slightly beyond the driver's own timeout so the driver can
        # raise ScannerTimeoutError before we declare it hung.
        try:
            bundle = future.result(timeout=timeout_s + 5.0)
        except FutureTimeout:
            # Driver is hung — keep a handle so the NEXT call rotates the worker
            # instead of queueing behind the stuck read.
            self._pending = future
            return ScannerTimeoutError(
                f"Driver.read_scan() hung beyond {timeout_s + 5.0:.0f}s hard limit."
            )
        except Exception as exc:
            return exc

        if isinstance(bundle, ScanBundle):
            return bundle
        return ScannerFrameError("Driver returned without result or error.")

    def _reconnect(self, initial: bool = False) -> None:
        """Disconnect and reconnect with exponential backoff.

        Raises ScannerUnavailableError when the budget is exhausted.
        """
        max_attempts = self._cfg.reconnect_attempts
        base_delay   = self._cfg.reconnect_delay_s

        if not initial:
            try:
                self._driver.disconnect()
            except Exception:
                pass

        for attempt in range(max_attempts):
            delay = min(base_delay * (2 ** attempt), _MAX_BACKOFF_S)
            if attempt > 0:
                log.info(
                    "Watchdog: reconnect attempt %d/%d in %.1fs (scanner=%s).",
                    attempt + 1, max_attempts, delay, self._cfg.scanner_id,
                )
                time.sleep(delay)

            try:
                self._driver.connect()
                self._stats.total_reconnects += 1
                self._stats.last_reconnect_at = time.time()
                self._stats.consecutive_errors = 0
                log.info(
                    "Watchdog: reconnect succeeded (attempt %d, scanner=%s).",
                    attempt + 1, self._cfg.scanner_id,
                )
                return
            except ScannerConnectionError as exc:
                log.warning(
                    "Watchdog: reconnect attempt %d/%d failed: %s",
                    attempt + 1, max_attempts, exc,
                )

        raise ScannerUnavailableError(
            f"Scanner '{self._cfg.scanner_id}' is unreachable after "
            f"{max_attempts} reconnect attempts. "
            f"Check hardware, cables, and driver configuration."
        )


__all__ = ["ConnectionWatchdog", "WatchdogStats"]
