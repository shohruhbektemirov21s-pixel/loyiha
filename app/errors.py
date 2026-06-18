"""Uniform error mapping. Keeps fail-closed semantics consistent across routers."""

from __future__ import annotations

from fastapi import HTTPException, status


def not_implemented(exc: Exception) -> HTTPException:
    """A service seam has no implementation yet -> 501 (never a faked result)."""
    return HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc))


def invalid_verdict(exc: Exception) -> HTTPException:
    """The VLM returned a verdict that violates the contract (e.g. hallucinated id).

    Treated as an upstream-dependency failure: 502, fail-closed. The operator is
    shown nothing rather than an unverifiable verdict.
    """
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"VLM verdict failed contract validation: {exc}",
    )
