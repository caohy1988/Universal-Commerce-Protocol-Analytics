"""Direct unit coverage for the segment-aware path-matching helper.

Lives in its own file (rather than inside test_middleware.py) so the
helper is exercised even in dev-only environments where the optional
Starlette dependency isn't installed.
"""

from __future__ import annotations

from ucp_analytics._path_match import path_matches_marker


class TestPathMatchesMarker:
    def test_exact_match(self):
        assert path_matches_marker("/orders", "/orders")

    def test_root_subpath(self):
        assert path_matches_marker("/orders/abc", "/orders")

    def test_mounted_exact(self):
        assert path_matches_marker("/api/v1/orders", "/orders")

    def test_mounted_subpath(self):
        assert path_matches_marker("/api/v1/orders/abc", "/orders")

    def test_segment_prefix_lookalike_rejected(self):
        # Word-bounded — `/orders` must not match `/reorders`,
        # `/orders-history`, `/orders-archive/...`.
        assert not path_matches_marker("/api/v1/reorders", "/orders")
        assert not path_matches_marker("/api/v1/orders-history", "/orders")
        assert not path_matches_marker("/api/v1/orders-archive/abc", "/orders")

    def test_segment_in_middle_lookalike_rejected(self):
        # `/orders` must be word-bounded by `/`, not concatenated.
        assert not path_matches_marker("/myorders/abc", "/orders")

    def test_dotted_well_known_path(self):
        # Markers that themselves contain `.` (e.g. /.well-known/ucp)
        # behave the same way under the helper.
        assert path_matches_marker("/.well-known/ucp", "/.well-known/ucp")
        assert path_matches_marker("/api/.well-known/ucp", "/.well-known/ucp")
        assert not path_matches_marker(
            "/api/.well-known/ucp-something", "/.well-known/ucp"
        )

    def test_catalog_does_not_match_catalogue(self):
        assert not path_matches_marker("/api/catalogue/search", "/catalog")
