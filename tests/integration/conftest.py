"""Integration-package conftest.

The DB-backed fixtures (``db_app``, ``db_client``, ``seed_scan``) and the
``requires_db`` marker now live in the root ``tests/conftest.py`` so they are
shared with the security suite too. This module re-exports ``requires_db`` for
the test files that import it from here.
"""

from __future__ import annotations

from tests.conftest import requires_db  # noqa: F401  (re-exported for imports)
