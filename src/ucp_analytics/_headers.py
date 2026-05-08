"""Header utilities shared by the tracker / middleware / HTTPX hook.

Lives in its own module so the HTTPX hook can use it without dragging in
the optional Starlette dependency that `middleware.py` imports at module
load time. Private (`_headers`) — not part of the package's public API.
"""

from __future__ import annotations

import re
from typing import Mapping, Optional

# RFC 9421 §2.3 — Signature-Input is a Structured Field Dictionary whose
# values carry a `keyid` parameter as a quoted string. Multiple sig
# labels (sig1, sig2, …) can appear in the same header value separated
# by commas; for analytics we only need one `keyid` to answer
# "is this row signed and by what key", so we extract the first match.
_SIGNATURE_INPUT_KEYID_RE = re.compile(r';\s*keyid\s*=\s*"([^"]+)"')


def lookup_header(headers: Optional[Mapping[str, str]], name: str) -> Optional[str]:
    """Case-insensitive header lookup.

    HTTP headers are case-insensitive per RFC 7230, but `dict(...)` of
    a Starlette / httpx headers object can leave casing in either form
    depending on the source. Walk the mapping ourselves so callers don't
    have to remember which casing the upstream framework normalized to.
    """
    if not headers:
        return None
    target = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == target:
            return value
    return None


def is_signed(headers: Optional[Mapping[str, str]]) -> bool:
    """True iff a complete RFC 9421 signature pair is present.

    UCP `signatures.md` requires both `Signature-Input` (metadata: covered
    components + keyid) and `Signature` (the signature value itself) to
    be present together. A half-signed exchange with only one of the two
    is malformed — counting it as signed would inflate the
    "% signed traffic" security KPI on every malformed request that ever
    reaches analytics.
    """
    sig_input = lookup_header(headers, "signature-input")
    sig = lookup_header(headers, "signature")
    return bool(sig_input and sig_input.strip() and sig and sig.strip())


def signature_keyid(headers: Optional[Mapping[str, str]]) -> Optional[str]:
    """Extract the first `keyid` parameter from `Signature-Input`.

    Returns the keyid string if a Signature-Input header is present and
    a `keyid="..."` parameter can be parsed from it; otherwise None. We
    only return one keyid even when the header carries multiple
    signature labels — analytics only needs one identifier to join
    against the JWK at `/.well-known/ucp`'s `signing_keys[]`.
    """
    raw = lookup_header(headers, "signature-input")
    if not raw:
        return None
    match = _SIGNATURE_INPUT_KEYID_RE.search(raw)
    return match.group(1) if match else None
