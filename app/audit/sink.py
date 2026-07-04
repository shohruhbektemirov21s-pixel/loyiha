"""PostgreSQL tamper-evident audit sink — replaces the logging-only stub.

Chain integrity model:

    event_hmac[n] = HMAC-SHA256(
        key  = XRAY_AUDIT_HMAC_KEY  (hex-encoded 32-byte secret from env)
        data = prev_hmac[n-1]           ← links to previous event
               || event_id.hex          ← UUID of this event
               || event_type            ← action label
               || scan_id_str           ← "" if none
               || operator_id_str       ← "" if none
               || created_at_iso        ← UTC ISO-8601 (from server clock)
               || canonical_payload     ← json.dumps(sorted, compact)
    )

    Genesis: prev_hmac[0] = "0" * 64

Any INSERT, UPDATE, or DELETE in the ``audit_events`` table breaks the chain
from that point; ``verify_chain()`` will report the first bad link.

Serialisation:
    Writes are serialised through an asyncio.Lock() to guarantee the
    prev_event_id → event_hmac chain is consistent within a single process.
    For multi-process deployments, each process maintains its own sub-chain
    rooted at process-startup time; verify_chain() handles this by following
    the ``prev_event_id`` FK rather than the ``seq`` column.

Separation of concerns:
    This sink only records. Updating ``scans.state`` is the ScanStore's job.
    The two are committed independently — eventual consistency between the
    audit table and the state table is acceptable; the audit log is the
    authoritative record.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditEvent

log = logging.getLogger("xray.audit")

_GENESIS_HMAC = "0" * 64

# Postgres transaction-level advisory-lock key for the audit chain's critical
# section. Any constant works as long as it is unique to this purpose. 0x78726179
# spells "xray" and fits a signed int4.
_AUDIT_LOCK_KEY = 0x78726179


def _canonical_payload(fields: dict[str, Any]) -> str:
    """Stable JSON serialisation of the event payload for HMAC input."""
    def _default(obj: Any) -> str:
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return repr(obj)

    return json.dumps(fields, sort_keys=True, separators=(",", ":"), default=_default)


def _compute_hmac(
    key_bytes: bytes,
    prev_hmac: str,
    event_id: UUID,
    event_type: str,
    scan_id: Any,
    operator_id: Any,
    created_at: datetime,
    payload_canonical: str,
) -> str:
    parts = [
        prev_hmac.encode(),
        event_id.hex.encode(),
        event_type.encode(),
        (str(scan_id) if scan_id else "").encode(),
        (str(operator_id) if operator_id else "").encode(),
        created_at.isoformat().encode(),
        payload_canonical.encode(),
    ]
    data = b"\x00".join(parts)
    return hmac.new(key_bytes, data, hashlib.sha256).hexdigest()


class PostgreSQLAuditSink:
    """Satisfies the ``AuditSink`` seam (``app.deps.AuditSink``).

    Constructed once during the lifespan via ``build_audit_sink()``;
    shared across requests via ``dependency_overrides``.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        hmac_key: bytes,
    ) -> None:
        self._session_factory = session_factory
        self._hmac_key = hmac_key
        self._lock = asyncio.Lock()

    async def record(self, event_type: str, *, scan_id: Any = None, **fields: Any) -> None:
        """Append one tamper-evident event.

        Correct across processes. The earlier implementation cached the chain
        tail (``_last_hmac``) in process memory; with uvicorn ``--workers 2``
        each worker held its own tail, so concurrent writers forked the chain
        and ``verify_chain`` (which walks the single seq-ordered linkage)
        reported a false break. Instead we now:

          1. take a transaction-level advisory lock so only one writer is in
             the chain's critical section at a time, across all workers;
          2. read the previous link from the DB tail inside that same
             transaction — never from per-process memory.

        The advisory lock is released automatically when the transaction
        commits, so the window is one INSERT wide. Audit volume is low
        (a handful of events per scan), so contention is negligible.
        """
        async with self._lock:  # cheap intra-process guard
            async with self._session_factory() as session:
                # Cross-process serialisation of the chain's critical section.
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:k)"),
                    {"k": _AUDIT_LOCK_KEY},
                )

                # Authoritative previous link = current DB tail (by seq).
                tail = (
                    await session.execute(
                        select(AuditEvent.event_id, AuditEvent.event_hmac)
                        .order_by(AuditEvent.seq.desc())
                        .limit(1)
                    )
                ).first()
                prev_event_id = tail.event_id if tail else None
                prev_hmac = tail.event_hmac if tail else _GENESIS_HMAC

                event_id = uuid.uuid4()
                created_at = datetime.now(timezone.utc)

                payload: dict[str, Any] = {}
                if fields.get("operator_id"):
                    payload["operator_id"] = str(fields["operator_id"])
                payload.update({k: v for k, v in fields.items() if k != "operator_id"})

                operator_id = fields.get("operator_id")
                canonical = _canonical_payload(payload)

                event_hmac = _compute_hmac(
                    self._hmac_key,
                    prev_hmac,
                    event_id,
                    event_type,
                    scan_id,
                    operator_id,
                    created_at,
                    canonical,
                )

                scan_uuid: UUID | None = None
                if scan_id is not None:
                    scan_uuid = UUID(str(scan_id)) if not isinstance(scan_id, UUID) else scan_id

                event = AuditEvent(
                    event_id=event_id,
                    prev_event_id=prev_event_id,
                    scan_id=scan_uuid,
                    operator_id=str(operator_id) if operator_id else None,
                    event_type=event_type,
                    payload=payload,
                    created_at=created_at,
                    event_hmac=event_hmac,
                )
                session.add(event)
                await session.commit()

                log.debug(
                    "audit %s scan=%s hmac=%s…",
                    event_type, scan_id, event_hmac[:12],
                )


# ---------------------------------------------------------------------------
# Chain verification (call from an admin endpoint or periodic health check)
# ---------------------------------------------------------------------------
async def verify_chain(
    session: AsyncSession,
    hmac_key: bytes,
    *,
    limit: int = 0,
) -> tuple[bool, str]:
    """Replay the audit chain and report the first broken link.

    Returns ``(True, "OK")`` if all links are intact, or
    ``(False, "<description of first bad link>")`` if not.
    """
    stmt = (
        select(AuditEvent)
        .order_by(AuditEvent.seq)
    )
    if limit:
        stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return True, "OK (empty chain)"

    prev_hmac = _GENESIS_HMAC
    prev_id: UUID | None = None

    for ev in rows:
        canonical = _canonical_payload(ev.payload)
        expected = _compute_hmac(
            hmac_key,
            prev_hmac,
            ev.event_id,
            ev.event_type,
            ev.scan_id,
            ev.operator_id,
            ev.created_at,
            canonical,
        )
        if not hmac.compare_digest(expected, ev.event_hmac):
            return False, (
                f"Chain broken at seq={ev.seq} event_id={ev.event_id} "
                f"event_type={ev.event_type}: "
                f"expected_hmac={expected[:16]}… stored={ev.event_hmac[:16]}…"
            )
        if prev_id is not None and ev.prev_event_id != prev_id:
            return False, (
                f"Chain link broken at seq={ev.seq}: "
                f"prev_event_id={ev.prev_event_id} expected={prev_id}"
            )
        prev_hmac = ev.event_hmac
        prev_id = ev.event_id

    return True, f"OK ({len(rows)} events verified)"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_audit_sink(
    session_factory: async_sessionmaker[AsyncSession],
    hmac_key_hex: str | None = None,
) -> PostgreSQLAuditSink:
    """Build the sink from the env-supplied HMAC key.

    If ``hmac_key_hex`` is None, reads from ``XRAY_AUDIT_HMAC_KEY``.
    The key must be exactly 64 hex characters (32 bytes). Fail-closed:
    if the key is missing, startup aborts — we never run without audit integrity.
    """
    raw = hmac_key_hex or os.environ.get("XRAY_AUDIT_HMAC_KEY", "")
    if not raw:
        raise RuntimeError(
            "XRAY_AUDIT_HMAC_KEY is not set. "
            "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    # Accept a hex key or any high-entropy secret (base64 from a platform
    # generator, etc.). See app.security_keys for the normalisation contract.
    from app.security_keys import normalise_key_bytes
    try:
        key_bytes = normalise_key_bytes(raw)
    except ValueError as exc:
        raise RuntimeError(f"XRAY_AUDIT_HMAC_KEY is invalid: {exc}") from exc
    return PostgreSQLAuditSink(session_factory, key_bytes)


__all__ = ["PostgreSQLAuditSink", "verify_chain", "build_audit_sink"]
