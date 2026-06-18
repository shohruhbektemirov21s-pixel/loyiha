# X-ray Assistant — serving layer

On-premise, air-gapped decision-support for customs X-ray operators.
**Decision-support only — the operator decides.**

```
contracts/   versioned Pydantic spine (the inter-layer contract) — see contracts/README.md
app/         FastAPI serving layer (this README)
```

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
# OpenAPI / live integration contract:  http://127.0.0.1:8000/docs
# Liveness:                              http://127.0.0.1:8000/health
```

## Endpoints (v1)

| Method | Path          | In                | Out               | Hop |
|--------|---------------|-------------------|-------------------|-----|
| POST   | `/v1/detect`  | `AcquisitionResult` | `DetectionResult` | Scanner → Detector |
| POST   | `/v1/verdict` | `VerdictRequest`    | `OperatorVerdict` | VLM → Console |
| GET    | `/health`     | —                 | status + contract version | — |

## How the layers plug in

The routers depend on **seams** (Protocols in `app/deps.py`), not concrete
models. Until a track ships, the default provider raises `ServiceNotImplemented`
→ **HTTP 501**: an unwired layer fails loudly, it never returns a faked result.

To wire a real implementation, override the provider:

```python
from app.deps import provide_detector
app.dependency_overrides[provide_detector] = lambda: MyRealDetector()
```

Tracks that can build in parallel against this skeleton today:
- **Detector** implements `Detector.detect`.
- **VLM** implements `VerdictGenerator.generate`.
- **Persistence/audit** implements `AuditSink.record` (default logs; swap for Postgres).
- **Console (NiceGUI)** mounts onto this same app and calls `/v1/*`.

## Status semantics (fail-closed)

| Code | Meaning |
|------|---------|
| 200  | Result produced and (for verdicts) passed contract validation. |
| 422  | Payload violated the contract — rejected before any handler logic. |
| 501  | That layer isn't implemented yet. |
| 502  | The VLM returned an invalid verdict (e.g. a hallucinated detection id). Operator sees nothing rather than an unverifiable verdict. |

## Proofs (run before integrating)

```bash
python -m contracts.v1._smoke   # contract round-trip + guardrails
python -m app._smoke_api        # serving skeleton end-to-end
```
