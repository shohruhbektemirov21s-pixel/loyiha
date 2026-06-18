"""State machine + atomic-transition unit tests (BO'SHLIQ-3).

Two layers, both pure unit (no real DB):

1. ``allowed_transition`` transition table — every legal edge, and every illegal
   edge raising ``InvalidTransitionError``. This is the authoritative lifecycle.

2. ``PostgresScanStore._transition`` compare-and-swap. We drive it with a fake
   async session whose ``UPDATE`` returns ``rowcount == 0`` to simulate a lost
   TOCTOU race, and assert it raises ``ConcurrentTransitionError`` (a subclass of
   ``InvalidTransitionError``) rather than clobbering the other writer. This is
   the atomicity guarantee the recently-added CAS introduced; the API layer maps
   it to 409 (tested at integration level in tests/integration/test_decision.py).

Deterministic, isolated — no I/O, no GPU, no Postgres.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.db.models import ScanState
from app.state.machine import (
    ConcurrentTransitionError,
    InvalidTransitionError,
    PostgresScanStore,
    allowed_transition,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------
_LEGAL_EDGES = [
    (ScanState.PENDING.value,   "detect.received",   ScanState.ANALYZING.value),
    (ScanState.ANALYZING.value, "detect.completed",  ScanState.ANALYZED.value),
    (ScanState.ANALYZING.value, "detect.failed",     ScanState.ERROR.value),
    (ScanState.ANALYZED.value,  "verdict.completed", ScanState.VERDICTED.value),
    (ScanState.ANALYZED.value,  "verdict.failed",    ScanState.ERROR.value),
    (ScanState.ANALYZED.value,  "feedback.banked",   ScanState.DECIDED.value),
    (ScanState.VERDICTED.value, "scan.opened",       ScanState.REVIEWING.value),
    (ScanState.REVIEWING.value, "feedback.banked",   ScanState.DECIDED.value),
]


class TestTransitionTable:
    @pytest.mark.parametrize("from_state,event,expected", _LEGAL_EDGES)
    def test_legal_transition(self, from_state, event, expected):
        assert allowed_transition(from_state, event) == expected

    @pytest.mark.parametrize(
        "from_state,event",
        [
            (ScanState.PENDING.value,   "feedback.banked"),   # can't decide before analysis
            (ScanState.DECIDED.value,   "feedback.banked"),   # terminal — no re-decide
            (ScanState.DECIDED.value,   "scan.opened"),       # terminal
            (ScanState.ERROR.value,     "detect.received"),   # terminal
            (ScanState.VERDICTED.value, "verdict.completed"), # already verdicted
            (ScanState.ANALYZED.value,  "scan.opened"),       # must verdict first
        ],
    )
    def test_illegal_transition_raises(self, from_state, event):
        with pytest.raises(InvalidTransitionError):
            allowed_transition(from_state, event)

    def test_unknown_event_raises(self):
        with pytest.raises(InvalidTransitionError):
            allowed_transition(ScanState.PENDING.value, "nonsense.event")

    def test_concurrent_error_is_invalid_transition_subclass(self):
        # Callers that already guard InvalidTransitionError must also catch a lost
        # race — so the subclass relationship is load-bearing.
        assert issubclass(ConcurrentTransitionError, InvalidTransitionError)


# ---------------------------------------------------------------------------
# Fakes for the CAS path
# ---------------------------------------------------------------------------
class _FakeScanRow:
    def __init__(self, state: str) -> None:
        self.state = state


class _FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeSession:
    """Minimal async session emulating SQLAlchemy's get()/execute() for _transition.

    ``update_rowcount`` controls what the UPDATE reports — 1 = CAS won, 0 = CAS
    lost (another writer advanced the row between our read and our write).
    """

    def __init__(self, state: str, update_rowcount: int = 1) -> None:
        self._row = _FakeScanRow(state)
        self._update_rowcount = update_rowcount
        self.executed = 0

    async def get(self, _model, _scan_id):
        return self._row

    async def execute(self, _stmt):
        self.executed += 1
        return _FakeResult(self._update_rowcount)


class TestAtomicTransitionCAS:
    @pytest.mark.asyncio
    async def test_winning_cas_advances_state(self):
        session = _FakeSession(ScanState.VERDICTED.value, update_rowcount=1)
        store = PostgresScanStore(session)  # type: ignore[arg-type]
        new_state = await store._transition(uuid4(), "scan.opened")
        assert new_state == ScanState.REVIEWING.value
        # The identity-mapped row is kept consistent with the DB write.
        assert session._row.state == ScanState.REVIEWING.value

    @pytest.mark.asyncio
    async def test_lost_cas_raises_concurrent_error(self):
        # rowcount == 0 -> the row no longer held the expected state: a concurrent
        # writer won. We must refuse, not overwrite.
        session = _FakeSession(ScanState.VERDICTED.value, update_rowcount=0)
        store = PostgresScanStore(session)  # type: ignore[arg-type]
        with pytest.raises(ConcurrentTransitionError):
            await store._transition(uuid4(), "scan.opened")
        # Row state must NOT have been clobbered by the loser.
        assert session._row.state == ScanState.VERDICTED.value

    @pytest.mark.asyncio
    async def test_double_decision_second_loses_cas(self):
        # Simulate two parallel "feedback.banked" on a REVIEWING scan: the first
        # wins (rowcount 1), the second sees rowcount 0 and is rejected — the API
        # turns this into 409, never a 500.
        first = _FakeSession(ScanState.REVIEWING.value, update_rowcount=1)
        assert await PostgresScanStore(first)._transition(  # type: ignore[arg-type]
            uuid4(), "feedback.banked"
        ) == ScanState.DECIDED.value

        second = _FakeSession(ScanState.REVIEWING.value, update_rowcount=0)
        with pytest.raises(ConcurrentTransitionError):
            await PostgresScanStore(second)._transition(  # type: ignore[arg-type]
                uuid4(), "feedback.banked"
            )

    @pytest.mark.asyncio
    async def test_invalid_event_raises_before_cas(self):
        # An illegal transition is rejected by allowed_transition() before any
        # UPDATE runs — so the session is never even executed against.
        session = _FakeSession(ScanState.DECIDED.value, update_rowcount=1)
        store = PostgresScanStore(session)  # type: ignore[arg-type]
        with pytest.raises(InvalidTransitionError):
            await store._transition(uuid4(), "feedback.banked")
        assert session.executed == 0

    @pytest.mark.asyncio
    async def test_missing_scan_raises_value_error(self):
        class _NoRowSession(_FakeSession):
            async def get(self, _model, _scan_id):
                return None

        store = PostgresScanStore(_NoRowSession(ScanState.PENDING.value))  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            await store._transition(uuid4(), "detect.received")
