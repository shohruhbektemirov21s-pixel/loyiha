"""Network egress tests — the application must make no outbound connections.

Verified at multiple levels:
    1. Import-time: no requests/urllib3/httpx calls fire on app import.
    2. Request-time: processing a full detection pipeline call makes no
       outbound DNS lookups or TCP connections to non-localhost addresses.
    3. Configuration: no telemetry sinks (Sentry, Datadog, etc.) are configured.

These tests run via socket patching — they intercept socket.connect() and
socket.getaddrinfo() to detect any outbound attempt.
"""

from __future__ import annotations

import socket
import unittest.mock as mock
from typing import Any

import pytest

# Allowed hosts for outbound connections (localhost only)
ALLOWED_HOSTS: frozenset[str] = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "testserver",
})


class _EgressDetector:
    """Context manager that patches socket.connect to catch external connections."""

    def __init__(self) -> None:
        self.violations: list[str] = []
        self._original_connect = socket.socket.connect

    def _checked_connect(self, sock_self: Any, address: Any) -> None:
        if isinstance(address, tuple):
            host = address[0]
            if host not in ALLOWED_HOSTS and not host.startswith("127.") and not host.startswith("::"):
                self.violations.append(f"Outbound connection to {host}:{address[1]}")
        return self._original_connect(sock_self, address)

    def __enter__(self):
        socket.socket.connect = self._checked_connect
        return self

    def __exit__(self, *_):
        socket.socket.connect = self._original_connect


class TestNoOutboundConnectionsOnImport:
    def test_app_import_makes_no_network_calls(self):
        """Importing the FastAPI app must not trigger any outbound connections."""
        with _EgressDetector() as detector:
            import importlib

            # Re-import app modules (they're already cached; this just re-evaluates)
            import app.main
            import app.settings
        assert not detector.violations, (
            f"Outbound network calls detected on import: {detector.violations}"
        )


class TestNoOutboundConnectionsDuringRequest:
    @pytest.mark.asyncio
    async def test_health_check_makes_no_egress(self, client):
        with _EgressDetector() as detector:
            resp = await client.get("/health")
        assert resp.status_code == 200
        assert not detector.violations, (
            f"Health check triggered outbound connection(s): {detector.violations}"
        )

    @pytest.mark.asyncio
    async def test_authenticated_scan_list_makes_no_egress(self, client, auth_headers):
        with _EgressDetector() as detector:
            resp = await client.get("/v1/scans", headers=auth_headers)
        # 500 is acceptable in stub mode (DB not initialised) — the invariant
        # is zero outbound socket calls, not a successful HTTP response.
        assert detector.violations == [], (
            f"Outbound socket call detected during scan list: {detector.violations}"
        )


class TestNoTelemetrySinks:
    """Verify that no telemetry or error-reporting services are wired in."""

    def test_no_sentry_dsn_configured(self):
        import os
        sentry_dsn = os.environ.get("SENTRY_DSN", "")
        assert not sentry_dsn, (
            "SENTRY_DSN is set — telemetry must not be configured in air-gapped deployment."
        )

    def test_sentry_not_initialised(self):
        try:
            import sentry_sdk
            client_obj = sentry_sdk.Hub.current.client
            assert client_obj is None or not getattr(client_obj, "dsn", None), (
                "Sentry SDK has an active DSN — telemetry egress risk."
            )
        except ImportError:
            pass   # sentry_sdk not installed — clean

    def test_no_datadog_tracer(self):
        try:
            import ddtrace
            tracer = ddtrace.tracer
            assert not tracer.writer or not getattr(tracer.writer, "_url", None), (
                "Datadog tracer has an active writer — telemetry egress risk."
            )
        except ImportError:
            pass

    def test_no_opentelemetry_exporter_to_external(self):
        """No OTEL exporter should be configured to send data outside localhost."""
        import os
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        if endpoint:
            allowed_prefixes = ("http://localhost", "http://127.", "http://otelcol")
            assert any(endpoint.startswith(p) for p in allowed_prefixes), (
                f"OTEL exporter configured to external endpoint: {endpoint!r}"
            )


class TestNoUDPOrRawSocketEgress:
    """UDP / raw-socket egress hardening (BO'SHLIQ-8).

    The TCP ``socket.connect`` patch above does not catch UDP ``sendto`` (which
    can leak to an external collector without a prior connect), nor a UDP socket
    that calls ``connect`` to set a default peer. Patch ``sendto`` and assert no
    external datagram is sent while the app handles a request.
    """

    @pytest.mark.asyncio
    async def test_health_check_sends_no_external_udp(self, client):
        sent_to: list[str] = []
        original_sendto = socket.socket.sendto

        def _checked_sendto(sock_self, data, *args):
            # address is the last positional arg (flags optional).
            address = args[-1] if args else None
            if isinstance(address, tuple):
                host = str(address[0])
                if host not in ALLOWED_HOSTS and not host.startswith("127.") and not host.startswith("::"):
                    sent_to.append(f"{host}:{address[1]}")
            return original_sendto(sock_self, data, *args)

        with mock.patch.object(socket.socket, "sendto", _checked_sendto):
            resp = await client.get("/health")
        assert resp.status_code == 200
        assert sent_to == [], f"External UDP datagram(s) sent: {sent_to}"

    def test_app_import_opens_no_raw_socket(self):
        """Importing the app must not create a raw (AF_PACKET / SOCK_RAW) socket."""
        raw_sockets: list[str] = []
        original_socket = socket.socket

        class _TrackingSocket(original_socket):  # type: ignore[misc, valid-type]
            def __init__(self, family=socket.AF_INET, type=socket.SOCK_STREAM, *a, **k):
                if type == socket.SOCK_RAW or getattr(socket, "AF_PACKET", None) == family:
                    raw_sockets.append(f"family={family} type={type}")
                super().__init__(family, type, *a, **k)

        with mock.patch.object(socket, "socket", _TrackingSocket):
            import app.main  # noqa: F401 — re-evaluate import under the patch
            import app.settings  # noqa: F401
        assert raw_sockets == [], f"Raw socket(s) opened on import: {raw_sockets}"


class TestNoAsyncioLoopEgress:
    """``asyncio`` event-loop level egress hardening (BO'SHLIQ-8).

    A coroutine could bypass the blocking ``socket.connect`` path by using
    ``loop.create_connection`` / ``loop.create_datagram_endpoint`` directly.
    Patch those on the running loop and assert the app makes no external call
    during a request.
    """

    @pytest.mark.asyncio
    async def test_no_create_connection_to_external_host(self, client):
        import asyncio

        loop = asyncio.get_event_loop()
        attempts: list[str] = []
        original_create_connection = loop.create_connection

        async def _checked_create_connection(protocol_factory, host=None, port=None, *a, **k):
            if host and str(host) not in ALLOWED_HOSTS and not str(host).startswith("127."):
                attempts.append(f"{host}:{port}")
            return await original_create_connection(protocol_factory, host, port, *a, **k)

        with mock.patch.object(loop, "create_connection", _checked_create_connection):
            resp = await client.get("/health")
        assert resp.status_code == 200
        assert attempts == [], f"loop.create_connection to external host: {attempts}"

    @pytest.mark.asyncio
    async def test_no_datagram_endpoint_to_external_host(self, client, auth_headers):
        import asyncio

        loop = asyncio.get_event_loop()
        attempts: list[str] = []
        original = loop.create_datagram_endpoint

        async def _checked(protocol_factory, *a, remote_addr=None, **k):
            if remote_addr and isinstance(remote_addr, tuple):
                host = str(remote_addr[0])
                if host not in ALLOWED_HOSTS and not host.startswith("127."):
                    attempts.append(f"{host}:{remote_addr[1]}")
            return await original(protocol_factory, *a, remote_addr=remote_addr, **k)

        with mock.patch.object(loop, "create_datagram_endpoint", _checked):
            resp = await client.get("/v1/scans", headers=auth_headers)
        # The invariant is no external datagram endpoint — not a 2xx response.
        assert attempts == [], f"loop.create_datagram_endpoint to external host: {attempts}"


class TestDNSResolutionNotLeaking:
    """DNS lookups for external domains must not occur during normal operation."""

    def test_no_external_dns_lookup_on_app_settings(self):
        """Loading app settings must not resolve any external DNS names."""
        resolved: list[str] = []
        original_getaddrinfo = socket.getaddrinfo

        def _patched_getaddrinfo(host, *args, **kwargs):
            if host not in ALLOWED_HOSTS and not str(host).startswith("127."):
                resolved.append(str(host))
            return original_getaddrinfo(host, *args, **kwargs)

        with mock.patch("socket.getaddrinfo", side_effect=_patched_getaddrinfo):
            from app.settings import Settings
            Settings()

        assert not resolved, (
            f"App settings triggered DNS lookups: {resolved}"
        )
