"""ConnectionWatchdog — resilient reconnect loop for scanner drivers.

Wraps any ScannerDriver and makes it self-healing:

  - Frame timeout: if ``read_scan`` hangs beyond ``frame_timeout_s``, the
    watchdog cancels the read (via a threading.Event) and reconnects.
  - Connection drops: if ``read_scan`` raises ScannerConnectionError,
    watchdog reconnects with exponential backoff.
  - Malformed frames: ScannerFrameError is logged and counted; after
    ``max_consecutive_errors`` in a row the watchdog reconnects.
  - Reconnect budget: after ``max_reconnect_attempts`` the watchdog raises
    ScannerUnavailableError — the pipeline must surface this to ops.

Design notes:
  - read_scan() is called from the pipeline's thread executor (blocking);
    the watchdog does NOT run its own thread.  It is purely a call-time
    decorator around driver.read_scan().
  - Reconnect itself is synchronous and inline with the calling thread.
    If you need non-blocking reconnects, run the pipeline in a dedicated
    thread or asyncio executor.
  - exponential_backoff: delay = min(base_delay * 2^attempt, max_delay_s)
"""

from __future__ import annotations

import logging
import threading
import time
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
        """Run driver.read_scan() in the current thread with a hard timeout.

        Returns a ScanBundle on success or the caught exception on failure.
        Uses a daemon thread + Event so the main thread is not blocked
        indefinitely if the driver hangs.
        """
        result_holder: list = []
        error_holder:  list = []
        done = threading.Event()

        def _worker():
            try:
                bundle = self._driver.read_scan(timeout_s=timeout_s)
                result_holder.append(bundle)
            except Exception as exc:
                error_holder.append(exc)
            finally:
                done.set()

        t = threading.Thread(target=_worker, daemon=True, name="acq-read")
        t.start()
        # Wait slightly beyond the driver's own timeout so the driver can
        # raise ScannerTimeoutError before we declare it hung.
        finished = done.wait(timeout=timeout_s + 5.0)

        if not finished:
            # Driver is hung — return a timeout error; the thread will
            # eventually die on its own when the next read fails.
            return ScannerTimeoutError(
                f"Driver.read_scan() hung beyond {timeout_s + 5.0:.0f}s hard limit."
            )

        if result_holder:
            return result_holder[0]
        if error_holder:
            return error_holder[0]
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
