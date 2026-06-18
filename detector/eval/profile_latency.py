"""Inference-latency profiler — *profile before you optimize*.

This is the measurement we run **before** touching quantization, pruning, or
TensorRT. It produces the latency budget the FastAPI serving layer must fit, and
the per-stage breakdown that tells us *where* the time actually goes — so we
optimize the bottleneck, not a guess.

Two modes:

  --mode serving   Profiles the REAL serving path: ``UltralyticsPredictor.
                   predict_frame`` (the same call the adapter makes). Reports
                   end-to-end wall time AND Ultralytics' own per-stage split
                   (preprocess / inference / postprocess). This is the number
                   the operator experiences — the one we stand behind.

  --mode ort       Profiles raw ONNX Runtime over the exported ``.onnx`` with
                   IOBinding, isolating device-side ``session.run`` from H2D/D2H
                   transfer, and lets you swap the execution provider
                   (CUDA / TensorRT / CPU). This is the apples-to-apples rig for
                   choosing a backend/precision later. NOTE: its preprocessing is
                   a minimal letterbox, NOT the production pipeline — use it for
                   relative EP/precision comparison, not as the deploy number.

Discipline baked in:
  * Warmup iterations are discarded (CUDA context init, cuDNN autotune, ORT
    graph optimization all land on the first calls and would poison the mean).
  * Every timed region is CUDA-synchronized — async kernels otherwise make the
    CPU clock lie.
  * We report the full distribution (p50/p90/p95/p99), not just the mean: tail
    latency is what a per-frame SLA is actually written against.
  * Full provenance is captured (weights sha256, imgsz, EP list, precision,
    versions, GPU, seed) and emitted as JSON so the run is reproducible and can
    be logged as an MLflow/W&B artifact — and so the FastAPI deploy config can
    be matched to exactly what was measured.

Run on the GPU / data box (needs the ML stack + an exported model):

    python -m detector.eval.profile_latency --weights weights/best.onnx \\
        --imgsz 1024 --image samples/real_scan.png --iters 300

Dry-run anywhere (no ML deps) to validate wiring before shipping to the GPU box:

    python -m detector.eval.profile_latency --weights weights/best.onnx --self-check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from detector.taxonomy import NET_CONF, NET_IOU

if TYPE_CHECKING:
    import numpy as np


# ---------------------------------------------------------------------------
# Stats — the distribution, not just the mean.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LatencyStats:
    """Summary of a sample of per-iteration latencies, in milliseconds."""

    n: int
    mean_ms: float
    std_ms: float
    min_ms: float
    p50_ms: float
    p90_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    throughput_fps: float  # 1000 / mean_ms * batch

    @staticmethod
    def from_samples(samples_ms: "list[float]", batch: int) -> "LatencyStats":
        import numpy as np

        a = np.asarray(samples_ms, dtype="float64")
        mean = float(a.mean())
        return LatencyStats(
            n=int(a.size),
            mean_ms=mean,
            std_ms=float(a.std(ddof=1)) if a.size > 1 else 0.0,
            min_ms=float(a.min()),
            p50_ms=float(np.percentile(a, 50)),
            p90_ms=float(np.percentile(a, 90)),
            p95_ms=float(np.percentile(a, 95)),
            p99_ms=float(np.percentile(a, 99)),
            max_ms=float(a.max()),
            throughput_fps=(1000.0 / mean * batch) if mean > 0 else 0.0,
        )


@dataclass
class ProfileReport:
    config: dict[str, Any]
    provenance: dict[str, Any]
    end_to_end: dict[str, Any]
    stages: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError as e:
        return f"<unreadable: {e}>"


def _cuda_sync() -> None:
    """Force outstanding GPU work to complete so the CPU clock tells the truth."""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
    except Exception:
        pass


def _gpu_name() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
    except Exception:
        pass
    return "<no cuda device visible>"


def _versions() -> dict[str, str]:
    out: dict[str, str] = {"python": platform.python_version()}
    for mod in ("numpy", "torch", "onnxruntime", "ultralytics"):
        try:
            import importlib.metadata as m

            out[mod] = m.version(mod)
        except Exception:
            out[mod] = "<not installed>"
    try:
        import torch

        out["torch_cuda"] = torch.version.cuda or "<cpu build>"
        out["cuda_available"] = str(torch.cuda.is_available())
    except Exception:
        out["cuda_available"] = "False"
    return out


def _load_frame(image: str | None, imgsz: int, seed: int) -> "np.ndarray":
    """Real held-out frame if given, else a seeded synthetic one.

    A real scan is strongly preferred: postprocess (NMS) time scales with the
    number of candidate boxes, which depends on actual content. Synthetic noise
    can over- or under-state postprocess. We warn loudly when synthetic.
    """
    import numpy as np

    if image:
        try:
            import cv2

            img = cv2.imread(image, cv2.IMREAD_COLOR)
            if img is None:
                raise FileNotFoundError(image)
            return img
        except ImportError:
            from PIL import Image  # fallback if cv2 absent

            return np.asarray(Image.open(image).convert("RGB"))[:, :, ::-1].copy()

    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(imgsz, imgsz, 3), dtype="uint8")


def _time_loop(call: Callable[[], Any], warmup: int, iters: int) -> "list[float]":
    """Warm up (discarded), then time ``iters`` synchronized calls in ms."""
    for _ in range(warmup):
        call()
    _cuda_sync()

    samples: list[float] = []
    for _ in range(iters):
        _cuda_sync()
        t0 = time.perf_counter()
        call()
        _cuda_sync()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return samples


# ---------------------------------------------------------------------------
# Mode: serving — the real predict_frame path (the number we deploy against).
# ---------------------------------------------------------------------------
def profile_serving(args: argparse.Namespace) -> ProfileReport:
    import numpy as np

    from detector.serving.predictor import UltralyticsPredictor

    frame = _load_frame(args.image, args.imgsz, args.seed)
    notes: list[str] = []
    if not args.image:
        notes.append(
            "SYNTHETIC input: postprocess/NMS time is not representative. "
            "Re-run with --image on a real held-out scan before quoting numbers."
        )

    predictor = UltralyticsPredictor(
        args.weights, conf=args.conf, iou=args.iou,
        imgsz=args.imgsz, device=args.device,
    )

    # End-to-end wall time of the exact call the adapter makes.
    e2e = _time_loop(lambda: predictor.predict_frame(frame), args.warmup, args.iters)

    # Per-stage split straight from Ultralytics' instrumentation. We re-run a
    # smaller timed batch and read results.speed each call. Access the raw model
    # so we get the Results object (predict_frame discards it).
    stage_samples = {"preprocess": [], "inference": [], "postprocess": []}
    for _ in range(min(args.iters, 100)):
        res = predictor._model.predict(  # noqa: SLF001 — intentional: read .speed
            frame, conf=args.conf, iou=args.iou, imgsz=args.imgsz,
            device=args.device, verbose=False,
        )[0]
        for k in stage_samples:
            stage_samples[k].append(float(res.speed.get(k, 0.0)))

    stages = {
        k: asdict(LatencyStats.from_samples(v, batch=1))
        for k, v in stage_samples.items()
    }

    return ProfileReport(
        config=_config_dict(args, mode="serving"),
        provenance=_provenance(args),
        end_to_end=asdict(LatencyStats.from_samples(e2e, batch=1)),
        stages=stages,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Mode: ort — raw ONNX Runtime, isolate device inference from transfer.
# ---------------------------------------------------------------------------
def profile_ort(args: argparse.Namespace) -> ProfileReport:
    import numpy as np
    import onnxruntime as ort

    notes = [
        "RAW-ORT mode: preprocessing is a minimal letterbox, NOT the production "
        "pipeline. Use this for relative EP/precision comparison only.",
    ]

    providers = _resolve_providers(args.providers)
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess = ort.InferenceSession(args.weights, sess_options=so, providers=providers)
    notes.append(f"active providers: {sess.get_providers()}")

    inp = sess.get_inputs()[0]
    name = inp.name
    # Static shape if exported with fixed dims; else fall back to (1,3,imgsz,imgsz).
    shape = [d if isinstance(d, int) else None for d in inp.shape]
    n = shape[0] if isinstance(shape[0], int) else args.batch
    c = shape[1] if isinstance(shape[1], int) else 3
    h = shape[2] if isinstance(shape[2], int) else args.imgsz
    w = shape[3] if isinstance(shape[3], int) else args.imgsz

    rng = np.random.default_rng(args.seed)
    x = rng.random((n, c, h, w), dtype="float32")  # already normalized [0,1)

    # session.run includes H2D copy of x + D2H copy of outputs.
    run = lambda: sess.run(None, {name: x})  # noqa: E731
    e2e = _time_loop(run, args.warmup, args.iters)

    stages: dict[str, Any] = {}
    # IOBinding: put input on device once, bind outputs on device → the timed
    # run is (close to) pure compute, no per-call transfer. The gap between this
    # and e2e is the transfer cost.
    try:
        io = sess.io_binding()
        ort_x = ort.OrtValue.ortvalue_from_numpy(x, "cuda", 0)
        io.bind_ortvalue_input(name, ort_x)
        for o in sess.get_outputs():
            io.bind_output(o.name, "cuda", 0)
        run_bound = lambda: sess.run_with_iobinding(io)  # noqa: E731
        compute = _time_loop(run_bound, args.warmup, args.iters)
        stages["device_compute"] = asdict(
            LatencyStats.from_samples(compute, batch=n)
        )
        notes.append(
            "device_compute = pure on-GPU run (IOBinding); "
            "end_to_end − device_compute ≈ H2D+D2H transfer overhead."
        )
    except Exception as e:  # CPU EP / no cuda allocator → skip, still report e2e
        notes.append(f"IOBinding device path unavailable: {e}")

    return ProfileReport(
        config=_config_dict(args, mode="ort"),
        provenance=_provenance(args),
        end_to_end=asdict(LatencyStats.from_samples(e2e, batch=n)),
        stages=stages,
        notes=notes,
    )


def _resolve_providers(spec: str) -> list:
    """Map a friendly --providers string to ORT provider list."""
    table = {
        "cuda": ["CUDAExecutionProvider", "CPUExecutionProvider"],
        "tensorrt": [
            "TensorrtExecutionProvider",
            "CUDAExecutionProvider",
            "CPUExecutionProvider",
        ],
        "cpu": ["CPUExecutionProvider"],
    }
    return table.get(spec, table["cuda"])


# ---------------------------------------------------------------------------
# Provenance & config
# ---------------------------------------------------------------------------
def _config_dict(args: argparse.Namespace, mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "weights": args.weights,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "conf": args.conf,
        "iou": args.iou,
        "device": args.device,
        "providers": args.providers,
        "warmup": args.warmup,
        "iters": args.iters,
        "seed": args.seed,
        "image": args.image or "<synthetic>",
    }


def _provenance(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "weights_sha256": _sha256(args.weights),
        "gpu": _gpu_name(),
        "platform": platform.platform(),
        "versions": _versions(),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _fmt_stats(label: str, s: dict[str, Any]) -> str:
    return (
        f"{label:<16} "
        f"mean {s['mean_ms']:7.2f}  p50 {s['p50_ms']:7.2f}  "
        f"p90 {s['p90_ms']:7.2f}  p95 {s['p95_ms']:7.2f}  "
        f"p99 {s['p99_ms']:7.2f}  max {s['max_ms']:7.2f} ms   "
        f"{s['throughput_fps']:6.1f} fps"
    )


def render(report: ProfileReport) -> str:
    lines = ["", "=" * 92, "INFERENCE LATENCY PROFILE", "=" * 92]
    c = report.config
    lines.append(
        f"mode={c['mode']}  imgsz={c['imgsz']}  batch={c['batch']}  "
        f"providers={c['providers']}  iters={c['iters']} (warmup {c['warmup']})"
    )
    lines.append(f"weights={c['weights']}  input={c['image']}")
    lines.append(f"gpu={report.provenance['gpu']}")
    lines.append(f"sha256={report.provenance['weights_sha256'][:16]}...")
    lines.append("-" * 92)
    lines.append(_fmt_stats("END-TO-END", report.end_to_end))
    if report.stages:
        lines.append("-" * 92)
        for k, s in report.stages.items():
            lines.append(_fmt_stats(k, s))
    if report.notes:
        lines.append("-" * 92)
        for note in report.notes:
            lines.append(f"! {note}")
    lines.append("=" * 92)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Profile detector inference latency.")
    p.add_argument("--weights", required=True, help="path to .onnx (or .pt)")
    p.add_argument("--mode", choices=["serving", "ort"], default="serving")
    p.add_argument("--imgsz", type=int, default=1024,
                   help="deploy default is 1024 (matches README/serving wiring)")
    p.add_argument("--batch", type=int, default=1, help="serving is per-frame (1)")
    p.add_argument("--conf", type=float, default=NET_CONF, help="recall-first low net")
    p.add_argument("--iou", type=float, default=NET_IOU)
    p.add_argument("--device", default=None, help="e.g. cuda:0; None = auto")
    p.add_argument("--providers", default="cuda",
                   choices=["cuda", "tensorrt", "cpu"], help="ort mode EP")
    p.add_argument("--warmup", type=int, default=20)
    p.add_argument("--iters", type=int, default=200)
    p.add_argument("--image", default=None, help="real held-out scan (preferred)")
    p.add_argument("--seed", type=int, default=1707)
    p.add_argument("--json", default=None, help="write machine-readable report here")
    p.add_argument("--self-check", action="store_true",
                   help="validate wiring/config without the ML stack; no profiling")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.self_check:
        cfg = _config_dict(args, mode=args.mode)
        print("[self-check] config resolved OK — ready to ship to the GPU box:")
        print(json.dumps(cfg, indent=2))
        print(f"[self-check] weights sha256: {_sha256(args.weights)}")
        print(f"[self-check] versions: {json.dumps(_versions())}")
        print("[self-check] note: actual profiling needs the GPU/ML stack.")
        return 0

    report = profile_serving(args) if args.mode == "serving" else profile_ort(args)
    print(render(report))

    if args.json:
        with open(args.json, "w") as f:
            json.dump(
                {
                    "config": report.config,
                    "provenance": report.provenance,
                    "end_to_end": report.end_to_end,
                    "stages": report.stages,
                    "notes": report.notes,
                },
                f,
                indent=2,
            )
        print(f"\n[json] report written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
