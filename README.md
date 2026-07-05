# X-ray Assistant

Decision-support for customs X-ray operators.
**Decision-support only — the operator decides.**

```
contracts/    versioned Pydantic spine (the inter-layer contract) — see contracts/README.md
acquisition/  scanner SDK adapters (Smiths / L3, DICOS) — integration stubs
detector/     YOLO-based detector serving + eval harness
vlm/          VLM verdict layer (Ollama / llama.cpp backends, Qwen-VL)
app/          FastAPI serving layer + auth + audit + DB (this README)
console/      React/Vite operator console (co-hosted by the backend at /)
camera/       webcam acquisition pipeline (demo/dev)
datalayer/    persistence layer
deploy/       docker-compose (on-prem), Render blueprint, Vast.ai GPU scripts
ops/          runbooks (incident response, disaster recovery, data diode)
```

## Run (dev)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
# Console (dev, hot-reload):  cd console && npm install && npm run dev
# OpenAPI (if XRAY_ENABLE_DOCS): http://127.0.0.1:8000/docs
# Liveness:                      http://127.0.0.1:8000/health
```

The backend also serves the **built** console same-origin: `cd console && npm run build`,
then open `http://127.0.0.1:8000/`. The cloud deploy (Render) listens on port **10000**
(`render.yaml`); all env vars are documented in `deploy/.env.example`.

## Endpoints (v1)

| Area | Method & Path |
|------|---------------|
| Pipeline | `POST /v1/detect` · `POST /v1/verdict` · `POST /v1/feedback` · `POST /v1/screen` |
| Scans | `GET /v1/scans` · `GET /v1/scans/{id}` · `GET /v1/scans/{id}/audit` · `POST /v1/scans/{id}/review` · `POST /v1/scans/{id}/decision` |
| Camera | `POST /v1/camera/capture` · `GET /v1/camera/live.mjpg` · `POST /v1/camera/stream/start|stop` · `GET /v1/camera/stream/status` · `GET /v1/scans/{id}/frames/{frame_id}` |
| Auth | `POST /v1/auth/login` (JWT; roles: operator < supervisor < admin) |
| Admin | `GET|POST /v1/admin/operators` · `DELETE /v1/admin/operators/{id}` · `GET /v1/admin/thresholds` · `PUT /v1/admin/thresholds/{category}` · `GET /v1/admin/audit/verify` |
| Live | `WS /v1/ws` (scan.analyzed / scan.flagged / scan.decided) |
| Ops | `GET /health` · `GET /metrics` (Prometheus, if installed) |

## How the layers plug in

The routers depend on **seams** (Protocols in `app/deps.py`), not concrete
models. Until a track ships, the default provider raises `ServiceNotImplemented`
→ **HTTP 501**: an unwired layer fails loudly, it never returns a faked result.

To wire a real implementation, override the provider:

```python
from app.deps import provide_detector
app.dependency_overrides[provide_detector] = lambda: MyRealDetector()
```

## Security model (fail-closed)

- **Auth is mandatory**: JWT HS256, bcrypt passwords, role hierarchy.
  `XRAY_AUTH_BYPASS` exists for local dev only — the app **refuses to boot**
  with it enabled when `XRAY_ENVIRONMENT=prod` (`app/settings.py`).
- **Prod requires persistence**: without `XRAY_DB_URL` in prod the app refuses
  to start rather than silently keep no records / no tamper-evident audit.
- **Audit chain**: HMAC-chained event log per scan (`GET /v1/admin/audit/verify`).

## Status semantics

| Code | Meaning |
|------|---------|
| 200  | Result produced and (for verdicts) passed contract validation. |
| 401/403 | Missing/invalid token, or role/lane not allowed. |
| 422  | Payload violated the contract — rejected before any handler logic. |
| 501  | That layer isn't implemented yet. |
| 502  | The VLM returned an invalid verdict (e.g. a hallucinated detection id). Operator sees nothing rather than an unverifiable verdict. |

## Tests

```bash
python -m pytest tests/unit tests/security -q     # fast, no DB needed
python -m pytest tests/integration -q             # needs XRAY_TEST_DB_URL (Postgres)
cd console && npx vitest run                      # console unit tests
```

CI (`.github/workflows/ci.yml`) runs lint + unit/security/contracts + integration
+ console + model-gate + perf jobs; a nightly GPU job gates detector recall.

## Deploys

- **Render + Vast.ai GPU** (current cloud demo): `render.yaml` blueprint —
  Postgres + FastAPI backend (co-hosted console) + static console. The backend
  start script boots the Vast GPU and opens an SSH tunnel to Ollama
  (`deploy/render_start.sh`). Set `ADMIN_PASSWORD`, `VAST_API_KEY`,
  `SSH_PRIVATE_KEY` in the Render dashboard.
- **On-prem / air-gapped** (target): `deploy/docker-compose.yml` with
  Prometheus + Grafana + Alertmanager, backup/restore scripts and systemd units.
