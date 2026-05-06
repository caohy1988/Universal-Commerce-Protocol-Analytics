"""Tests for UCPAnalyticsMiddleware path filtering.

Lightweight tests that bypass FastAPI/Starlette wiring and exercise the
detector logic directly. The full middleware integration is exercised
end-to-end elsewhere; these tests pin the behavior the PR-9 reviewer
flagged: the server-side detector must accept mounted UCP base paths
the same way the HTTPX client hook already does, without falsely
matching segment-prefix lookalikes like /api/catalogue/search.
"""

from __future__ import annotations

import pytest

# starlette ships only in the [fastapi] extra; tests that exercise the
# middleware module skip cleanly in dev-only environments.
pytest.importorskip("starlette")

from ucp_analytics._path_match import path_matches_marker  # noqa: E402
from ucp_analytics.middleware import UCPAnalyticsMiddleware  # noqa: E402


def _matches(path: str) -> bool:
    """Replicates the dispatch() fast-path filter for UCPAnalyticsMiddleware."""
    return any(
        path_matches_marker(path, p) for p in UCPAnalyticsMiddleware.UCP_PATH_PREFIXES
    )


class TestMiddlewareDetectorMatchesMountedPaths:
    """OpenAPI paths in the UCP spec are relative to the platform-advertised
    REST endpoint, so real deployments mount the marker segments under a
    prefix. The middleware detector must accept these the same way the
    HTTPX hook does — without this, server-side traffic at any non-root
    mount silently bypasses analytics."""

    def test_root_catalog_matches(self):
        assert _matches("/catalog/search")
        assert _matches("/catalog/lookup")
        assert _matches("/catalog/product")

    def test_mounted_catalog_matches(self):
        assert _matches("/ucp/v1/catalog/search")
        assert _matches("/api/v2/catalog/lookup")
        assert _matches("/merchant/api/ucp/v1/catalog/product")

    def test_mounted_checkout_matches(self):
        assert _matches("/ucp/v1/checkout-sessions")
        assert _matches("/api/v2/checkout-sessions/chk_abc/complete")

    def test_mounted_carts_matches(self):
        assert _matches("/ucp/v1/carts")
        assert _matches("/merchant/api/ucp/v1/carts/cart_abc")

    def test_mounted_orders_matches(self):
        assert _matches("/ucp/v1/orders/order_abc")

    def test_mounted_identity_matches(self):
        assert _matches("/ucp/v1/identity")
        assert _matches("/api/v2/identity/callback")

    def test_unrelated_paths_do_not_match(self):
        assert not _matches("/healthz")
        assert not _matches("/api/users")
        assert not _matches("/static/main.css")
        assert not _matches("/")


class TestMiddlewareDetectorRejectsNearMisses:
    """Segment-prefix lookalikes that share a substring with a UCP marker
    must not match. A loose `marker in path` filter would record these as
    UCP traffic, then consume their bodies and emit noisy generic events."""

    def test_catalog_does_not_match_catalogue(self):
        assert not _matches("/api/catalogue/search")
        assert not _matches("/v1/catalogue")

    def test_orders_does_not_match_orders_history(self):
        assert not _matches("/api/orders-history")
        assert not _matches("/api/reorders")

    def test_identity_does_not_match_identity_card(self):
        assert not _matches("/api/identity-card")
        assert not _matches("/api/identitymanager")

    def test_carts_does_not_match_carts_preview(self):
        assert not _matches("/api/carts-preview")
        assert not _matches("/api/discards")

    def test_checkout_sessions_does_not_match_lookalike(self):
        assert not _matches("/api/checkout-sessions-archive")


# Direct unit coverage of path_matches_marker lives in tests/test_path_match.py
# so it runs even in environments without the optional Starlette dependency.
