#!/usr/bin/env python3
"""model-registry/registry.py — CLI for managing model weight manifests.

Commands:
    push   <type> <file>      Register a model file in the local registry
    verify <type>             Verify the registered model's SHA-256 checksum
    list                      List all registered models
    status                    Print registry status for healthcheck

Usage:
    python registry.py push detector /var/lib/xray/models/detector.onnx
    python registry.py verify detector
    python registry.py list
    python registry.py status   # exits 0 if all OK, 1 if any fail
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path(os.getenv("MODEL_REGISTRY_PATH", "/var/lib/xray/models/registry.json"))


# ---------------------------------------------------------------------------
# Registry I/O
# ---------------------------------------------------------------------------

def _load() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {"schema": "1.0", "models": {}}
    try:
        return json.loads(REGISTRY_PATH.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: registry.json is corrupt: {e}", file=sys.stderr)
        sys.exit(1)


def _save(registry: dict[str, Any]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(registry, indent=2))
    tmp.replace(REGISTRY_PATH)


def _sha256(path: Path, chunk: int = 65536) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_push(args: argparse.Namespace) -> int:
    model_type = args.type
    model_path = Path(args.file)

    if not model_path.exists():
        print(f"ERROR: file not found: {model_path}", file=sys.stderr)
        return 1

    print(f"Computing SHA-256 for {model_path} …", flush=True)
    sha256 = _sha256(model_path)
    size   = model_path.stat().st_size
    now    = datetime.now(timezone.utc).isoformat()

    registry = _load()
    prev = registry["models"].get(model_type)
    registry["models"][model_type] = {
        "type":        model_type,
        "name":        args.name or model_path.name,
        "version":     args.version or "unknown",
        "file":        str(model_path),
        "sha256":      sha256,
        "size_bytes":  size,
        "registered_at": now,
        "previous":    prev,
    }
    _save(registry)

    print(f"Registered {model_type}:")
    print(f"  File:    {model_path}")
    print(f"  SHA-256: {sha256}")
    print(f"  Size:    {size / 1e6:.1f} MB")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    registry = _load()
    models = registry.get("models", {})

    if args.type and args.type not in models:
        print(f"ERROR: model type '{args.type}' not in registry", file=sys.stderr)
        return 1

    targets = {args.type: models[args.type]} if args.type else models
    all_ok  = True

    for mtype, info in targets.items():
        path = Path(info["file"])
        if not path.exists():
            print(f"FAIL  {mtype}: file missing ({path})")
            all_ok = False
            continue

        expected = info["sha256"]
        actual   = _sha256(path)

        if expected == actual:
            print(f"OK    {mtype}: {path.name}  SHA-256 {actual[:16]}…")
        else:
            print(f"FAIL  {mtype}: SHA-256 MISMATCH")
            print(f"      expected: {expected}")
            print(f"      actual:   {actual}")
            all_ok = False

    return 0 if all_ok else 1


def cmd_list(_args: argparse.Namespace) -> int:
    registry = _load()
    models   = registry.get("models", {})

    if not models:
        print("No models registered.")
        return 0

    print(f"{'TYPE':<16} {'VERSION':<12} {'SHA-256':<20} {'SIZE':>10}  {'REGISTERED'}")
    print("-" * 80)
    for mtype, info in models.items():
        sha_short = info.get("sha256", "")[:16] + "…"
        size_mb   = f"{info.get('size_bytes', 0) / 1e6:.1f} MB"
        reg_at    = info.get("registered_at", "?")[:19]
        version   = info.get("version", "?")[:11]
        print(f"{mtype:<16} {version:<12} {sha_short:<20} {size_mb:>10}  {reg_at}")

    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    """Exit 0 if all registered models pass checksum verification."""
    registry = _load()
    models   = registry.get("models", {})

    if not models:
        print("WARNING: no models registered", file=sys.stderr)
        return 1

    all_ok = True
    for mtype, info in models.items():
        path = Path(info["file"])
        if not path.exists():
            print(f"MISSING {mtype}: {path}", file=sys.stderr)
            all_ok = False
            continue
        if _sha256(path) != info["sha256"]:
            print(f"CORRUPT {mtype}: SHA-256 mismatch", file=sys.stderr)
            all_ok = False

    if all_ok:
        print(f"OK: {len(models)} model(s) verified")
    return 0 if all_ok else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="X-ray model registry")
    sub    = parser.add_subparsers(dest="cmd", required=True)

    p_push = sub.add_parser("push", help="Register a model file")
    p_push.add_argument("type",    help="Model type: detector | vlm")
    p_push.add_argument("file",    help="Path to model file")
    p_push.add_argument("--name",    default=None)
    p_push.add_argument("--version", default=None)

    p_ver = sub.add_parser("verify", help="Verify SHA-256 of registered model(s)")
    p_ver.add_argument("type", nargs="?", default=None, help="Model type (omit for all)")

    sub.add_parser("list",   help="List registered models")
    sub.add_parser("status", help="Exit 0 if all checksums pass (for healthcheck)")

    args = parser.parse_args()

    dispatch = {
        "push":   cmd_push,
        "verify": cmd_verify,
        "list":   cmd_list,
        "status": cmd_status,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
