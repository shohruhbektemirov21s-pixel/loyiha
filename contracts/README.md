# Contract spine — `contracts/v1`

The single source of truth for the Pydantic messages exchanged between the four
layers. Freeze this, and the four specialist tracks build in parallel against a
stable spec. Import from the pinned version only:

```python
from contracts.v1 import AcquisitionResult, DetectionResult, VerdictRequest, OperatorVerdict
```

## The three hops

| # | Producer → Consumer | Message | FastAPI endpoint |
|---|---------------------|---------|------------------|
| 1 | Scanner/ingest → Detector | `AcquisitionResult` | `POST /v1/detect` (body) |
| 2 | Detector → VLM | `DetectionResult` | produced by `/v1/detect`, embedded in next |
| 3 | VLM → Console | `VerdictRequest` → `OperatorVerdict` | `POST /v1/verdict` |

`scan_id` is the one correlation key across all hops and the audit log.

## Load-bearing invariants (enforced in code, not just docs)

1. **The VLM is a verbalizer, never a detector.** `VerdictRequest` *embeds* the
   full `DetectionResult`. There is no contract field for "raw image, no
   detections" — you cannot invoke the VLM without handing it the detector's
   findings. This is the structural form of *the VLM is never the primary
   detector*.
2. **The VLM cannot invent a detection.** `validate_referential_integrity()`
   rejects any verdict whose `per_detection[*].detection_id` was not in the
   request. Call it before persisting/serving a verdict.
3. **Every verdict is decision-support only.** `OperatorVerdict.decision_support_only`
   is `Literal[True]` — any other value is a validation error. The operator
   decides; the system advises.
4. **Fail-closed wire format.** All messages `forbid` unknown fields and are
   `frozen` (immutable). Drift or a typo'd field is rejected, not silently
   accepted — appropriate for a single-deployment, security-critical system.
5. **Geometry is checked.** Boxes are pixel-authoritative and validated to fit
   inside their referenced frame; detections must reference a frame that exists.
6. **Bytes travel by reference.** Images are `StorageRef` (URI + SHA-256 + size)
   into the local encrypted store, never inline base64. Keeps the API async and
   the audit log small; the hash proves which bytes were analyzed.

Run the executable proof of all of the above:

```bash
python -m contracts.v1._smoke
```

## Versioning policy

- **Additive, optional** field within a major version → edit `v1` in place.
- **Breaking** change (rename/remove/retype/new-required) → new `contracts/v2`
  package, served under `/v2`, run side-by-side during migration. Never mutate a
  released field's meaning.
- `schema_version` is pinned on the wire (`Literal["1.0"]`) so a consumer
  hard-rejects a payload it was not built against.

## Open assumptions (flag if wrong — they shaped these schemas)

- **Detector ownership** unstated → `native_label` is a free string mapped onto
  the shared `ThreatCategory` enum, so the taxonomy survives a model swap.
- **Scanner format** unstated → `ImageModality` covers single/dual-energy/
  multi-view; `media_type` defaults to `image/tiff`. Adjust if proprietary/DICOM.
- **Throughput** unstated → reference-by-URI + async-friendly messages assume
  near-real-time, not batch. Holds either way.
- **`pixel_spacing_mm`** is optional — populate it if the scanner reports it; it
  unlocks real-world-size reasoning in the VLM prompt and console.
