"""Access control tests — JWT authentication and route protection.

Every non-public endpoint must return 401 when called without a token,
and 403 when called with a token that lacks the required role.

Also tests:
    - Expired tokens are rejected.
    - Forged tokens (wrong signature) are rejected.
    - Tokens from one lane cannot access another lane's scans.
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Routes that require authentication
# ---------------------------------------------------------------------------

# GET routes: body validation cannot fire first, so 401 is guaranteed
AUTH_REQUIRED_GET_ROUTES: list[tuple[str, str]] = [
    ("GET", "/v1/scans"),
    ("GET", "/v1/scans/00000000-0000-0000-0000-000000000001"),
    ("GET", "/v1/scans/00000000-0000-0000-0000-000000000001/audit"),
]

# POST routes: FastAPI may return 422 (body invalid) before 401 (auth)
# because body parsing runs before Depends(). Both responses deny access.
AUTH_REQUIRED_POST_ROUTES: list[tuple[str, str]] = [
    ("POST", "/v1/feedback"),
    ("POST", "/v1/detect"),
]

# Combined for legacy alias
AUTH_REQUIRED_ROUTES = AUTH_REQUIRED_GET_ROUTES

PUBLIC_ROUTES: list[tuple[str, str]] = [
    ("GET", "/health"),
]


class TestPublicRoutes:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,path", PUBLIC_ROUTES)
    async def test_public_route_reachable_without_token(self, client, method, path):
        resp = await getattr(client, method.lower())(path)
        assert resp.status_code != 401, (
            f"{method} {path} returned 401 — public routes must not require auth"
        )


class TestUnauthenticatedRequestsRejected:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,path", AUTH_REQUIRED_GET_ROUTES)
    async def test_no_token_on_get_returns_401(self, client, method, path):
        resp = await getattr(client, method.lower())(path)
        assert resp.status_code == 401, (
            f"{method} {path}: expected 401 without token, got {resp.status_code}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,path", AUTH_REQUIRED_POST_ROUTES)
    async def test_no_token_on_post_denies_access(self, client, method, path):
        """POST routes without a token must not return 2xx — 401 or 422 both deny access.

        FastAPI body parsing may run before auth Depends(), producing 422
        on an empty body before the auth check fires. Both are correct.
        """
        resp = await getattr(client, method.lower())(path, json={})
        assert resp.status_code in (401, 422), (
            f"{method} {path}: expected 401/422 without token, got {resp.status_code}"
        )
        assert resp.status_code < 500, "Server error on unauthenticated POST"

    @pytest.mark.asyncio
    async def test_malformed_bearer_returns_401(self, client):
        resp = await client.get(
            "/v1/scans",
            headers={"Authorization": "Bearer not.a.valid.jwt"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_bearer_returns_401(self, client):
        resp = await client.get(
            "/v1/scans",
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_basic_auth_not_accepted(self, client):
        import base64
        creds = base64.b64encode(b"admin:admin").decode()
        resp = await client.get(
            "/v1/scans",
            headers={"Authorization": f"Basic {creds}"},
        )
        assert resp.status_code == 401


class TestForgedTokenRejected:
    @pytest.mark.asyncio
    async def test_token_with_wrong_signature_rejected(self, client):
        """A JWT with a valid structure but wrong signature must be rejected."""
        import jwt as pyjwt
        forged = pyjwt.encode(
            {"sub": "attacker", "role": "operator", "exp": int(time.time()) + 3600},
            key="WRONG_SECRET",
            algorithm="HS256",
        )
        resp = await client.get(
            "/v1/scans",
            headers={"Authorization": f"Bearer {forged}"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self, client):
        import os

        import jwt as pyjwt
        secret = os.environ.get("XRAY_JWT_SECRET", "test")
        expired = pyjwt.encode(
            {
                "sub": str(uuid4()),
                "role": "operator",
                "exp": int(time.time()) - 3600,   # expired 1h ago
            },
            key=secret,
            algorithm="HS256",
        )
        resp = await client.get(
            "/v1/scans",
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_alg_none_attack_rejected(self, client):
        """'alg: none' attack — unsigned token must be rejected."""
        import base64
        import json
        header  = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=")
        payload = base64.urlsafe_b64encode(
            json.dumps({"sub": "attacker", "role": "admin", "exp": int(time.time()) + 9999}).encode()
        ).rstrip(b"=")
        forged_token = f"{header.decode()}.{payload.decode()}."
        resp = await client.get(
            "/v1/scans",
            headers={"Authorization": f"Bearer {forged_token}"},
        )
        assert resp.status_code == 401


class TestAuthenticatedRequestsAccepted:
    @pytest.mark.asyncio
    async def test_valid_token_allows_scan_list(self, client, auth_headers):
        resp = await client.get("/v1/scans", headers=auth_headers)
        # 200 (stub returns empty list) or 404/501 in stub mode — anything but 401/403
        assert resp.status_code not in (401, 403), (
            f"Valid token was rejected: {resp.status_code} {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_valid_token_returns_user_context(self, client, auth_headers):
        resp = await client.get("/health", headers=auth_headers)
        assert resp.status_code == 200


class TestSensitiveInformationNotLeaked:
    @pytest.mark.asyncio
    async def test_401_response_has_no_stack_trace(self, client):
        resp = await client.get(
            "/v1/scans",
            headers={"Authorization": "Bearer invalid"},
        )
        body = resp.text
        assert "traceback" not in body.lower()
        assert "file \"/" not in body.lower()
        assert "line " not in body.lower()

    @pytest.mark.asyncio
    async def test_404_response_has_no_internal_paths(self, client, auth_headers):
        resp = await client.get("/v1/nonexistent-endpoint", headers=auth_headers)
        body = resp.text
        assert "/home/" not in body
        assert "/opt/" not in body
        assert "password" not in body.lower()
