"""Segment-aware path matching shared by the REST middleware and HTTPX hook.

Lives in its own module so the HTTPX hook can use it without dragging in
the optional Starlette dependency that `middleware.py` imports at module
load time. Private (`_path_match`) — not part of the package's public API.
"""

from __future__ import annotations


def path_matches_marker(path: str, marker: str) -> bool:
    """Segment-aware match for a UCP path marker.

    True if `path` is exactly `marker`, has `marker` as a leading segment
    (`/marker/...`), has `marker` as the trailing segment under a mount
    (`/api/v1/marker`), or contains `marker` as an interior segment
    (`/api/v1/marker/{id}`). False for near-misses like `/api/catalogue/...`
    against `/catalog`, or `/api/orders-history` against `/orders`.
    """
    return (
        path == marker
        or path.startswith(marker + "/")
        or path.endswith(marker)
        or marker + "/" in path
    )
