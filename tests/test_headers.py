"""Direct unit coverage for the _headers helper.

Lives in its own file (no Starlette dependency) so the helper is
exercised even in dev-only environments where the optional [fastapi]
extra isn't installed.
"""

from __future__ import annotations

from ucp_analytics._headers import (
    is_signed,
    lookup_header,
    signature_keyid,
    ucp_agent_profile_url,
    webhook_id,
    webhook_timestamp_iso,
)


class TestLookupHeader:
    def test_exact_case(self):
        assert lookup_header({"Signature-Input": "x"}, "Signature-Input") == "x"

    def test_lowercase_input(self):
        assert lookup_header({"signature-input": "x"}, "Signature-Input") == "x"

    def test_uppercase_input(self):
        assert lookup_header({"SIGNATURE-INPUT": "x"}, "Signature-Input") == "x"

    def test_mixed_case(self):
        assert lookup_header({"sIgNaTuRe-InPuT": "x"}, "signature-input") == "x"

    def test_missing(self):
        assert lookup_header({"other": "y"}, "Signature-Input") is None

    def test_none_headers(self):
        assert lookup_header(None, "Signature-Input") is None

    def test_empty_headers(self):
        assert lookup_header({}, "Signature-Input") is None


class TestIsSigned:
    def test_both_signature_input_and_signature_present(self):
        # UCP signatures.md requires both headers to be present together;
        # only the pair is a real signature.
        headers = {
            "signature-input": (
                'sig1=("@method" "@path");keyid="key-1";created=1618884475'
            ),
            "signature": "sig1=:abc==:",
        }
        assert is_signed(headers) is True

    def test_pair_present_uppercase(self):
        # Real-world middleware can hand off either casing; both work.
        assert (
            is_signed(
                {
                    "Signature-Input": 'sig1=();keyid="k1"',
                    "Signature": "sig1=:abc==:",
                }
            )
            is True
        )

    def test_signature_input_only_is_not_signed(self):
        # Half-signed exchange: metadata header without the actual value.
        # Must NOT count as signed — counting it would inflate the
        # "% signed traffic" KPI on every malformed request.
        assert is_signed({"signature-input": 'sig1=();keyid="k1"'}) is False

    def test_signature_only_is_not_signed(self):
        # The other half: signature value without the metadata header that
        # advertises covered components and keyid.
        assert is_signed({"signature": "sig1=:abc==:"}) is False

    def test_absent(self):
        assert is_signed({"content-type": "application/json"}) is False

    def test_none(self):
        assert is_signed(None) is False

    def test_empty_signature_input_value(self):
        # Empty Signature-Input — not a real signature even if Signature
        # is somehow present.
        assert is_signed({"signature-input": "", "signature": "sig1=:abc==:"}) is False

    def test_empty_signature_value(self):
        assert (
            is_signed({"signature-input": 'sig1=();keyid="k1"', "signature": ""})
            is False
        )

    def test_whitespace_only_values(self):
        assert is_signed({"signature-input": "   ", "signature": "   "}) is False


class TestSignatureKeyid:
    def test_simple_keyid(self):
        headers = {
            "Signature-Input": (
                'sig1=("@method" "@path" "host");keyid="key-1";created=1618884475'
            )
        }
        assert signature_keyid(headers) == "key-1"

    def test_keyid_first_among_multiple_sig_labels(self):
        # RFC 9421 permits multiple signature labels; we take the first
        # parsable keyid for analytics.
        headers = {
            "Signature-Input": (
                'sig1=();keyid="key-A";alg="ecdsa-p256-sha256", sig2=();keyid="key-B"'
            )
        }
        assert signature_keyid(headers) == "key-A"

    def test_keyid_with_dotted_identifier(self):
        # Real UCP keyids look like JWK kids — alphanumeric / dashes /
        # dots / colons.
        headers = {
            "Signature-Input": ('sig1=();keyid="dev.merchant.example/key-2026-04-08"')
        }
        assert signature_keyid(headers) == "dev.merchant.example/key-2026-04-08"

    def test_no_keyid_parameter(self):
        # Header present but missing keyid (malformed, non-UCP, etc.).
        headers = {"Signature-Input": 'sig1=("@method")'}
        assert signature_keyid(headers) is None

    def test_no_signature_input_header(self):
        assert signature_keyid({"content-type": "application/json"}) is None

    def test_none(self):
        assert signature_keyid(None) is None

    def test_no_keyid_when_only_other_params_present(self):
        # `keyid` must be its own structured-field parameter; a header
        # with only other parameters (no keyid) returns None.
        assert signature_keyid({"Signature-Input": 'sig1=();foo="bar"'}) is None


class TestWebhookId:
    def test_present(self):
        assert webhook_id({"Webhook-Id": "evt_abc123"}) == "evt_abc123"

    def test_lowercase(self):
        assert webhook_id({"webhook-id": "evt_xyz"}) == "evt_xyz"

    def test_strips_whitespace(self):
        assert webhook_id({"Webhook-Id": "  evt_abc  "}) == "evt_abc"

    def test_empty_value_returns_none(self):
        assert webhook_id({"Webhook-Id": ""}) is None
        assert webhook_id({"Webhook-Id": "   "}) is None

    def test_absent(self):
        assert webhook_id({"content-type": "application/json"}) is None

    def test_none(self):
        assert webhook_id(None) is None


class TestWebhookTimestampIso:
    """UCP order.md documents Webhook-Timestamp as Unix seconds, not
    ISO 8601. Parsing it as ISO 8601 (the obvious-but-wrong default)
    would silently drop the column on every webhook."""

    def test_unix_seconds_parses_to_iso_utc(self):
        # 2026-01-01T00:00:00Z = 1767225600
        result = webhook_timestamp_iso({"Webhook-Timestamp": "1767225600"})
        assert result == "2026-01-01T00:00:00+00:00"

    def test_lowercase_header_name(self):
        assert webhook_timestamp_iso({"webhook-timestamp": "1770000000"}) is not None

    def test_strips_whitespace(self):
        assert (
            webhook_timestamp_iso({"Webhook-Timestamp": "  1767225600  "})
            == "2026-01-01T00:00:00+00:00"
        )

    def test_iso_input_not_misparsed(self):
        # An ISO 8601 string in this header is malformed per spec.
        # Don't raise — return None so the row still flows.
        assert (
            webhook_timestamp_iso({"Webhook-Timestamp": "2026-01-01T00:00:00Z"}) is None
        )

    def test_garbage_returns_none(self):
        assert webhook_timestamp_iso({"Webhook-Timestamp": "not-a-number"}) is None

    def test_empty_value(self):
        assert webhook_timestamp_iso({"Webhook-Timestamp": ""}) is None
        assert webhook_timestamp_iso({"Webhook-Timestamp": "   "}) is None

    def test_absent(self):
        assert webhook_timestamp_iso({"content-type": "application/json"}) is None

    def test_none(self):
        assert webhook_timestamp_iso(None) is None

    def test_negative_unix_seconds_pre_epoch(self):
        # Pre-1970 timestamps are unusual but technically valid Unix
        # seconds. Parse without raising.
        result = webhook_timestamp_iso({"Webhook-Timestamp": "-86400"})
        assert result == "1969-12-31T00:00:00+00:00"

    def test_extreme_value_returns_none(self):
        # OverflowError / OSError on platform-out-of-range — return None
        # rather than raising, so a single bad sender doesn't crash a
        # row insert.
        assert (
            webhook_timestamp_iso({"Webhook-Timestamp": "999999999999999999999"})
            is None
        )


class TestUcpAgentProfileUrl:
    """UCP-Agent is an RFC 8941 Structured Field Dictionary with a
    `profile` member that's a quoted-string URI per checkout-rest.md."""

    def test_canonical_form(self):
        # The example from checkout-rest.md.
        assert (
            ucp_agent_profile_url(
                {"UCP-Agent": 'profile="https://platform.example/profile"'}
            )
            == "https://platform.example/profile"
        )

    def test_lowercase_header_name(self):
        assert (
            ucp_agent_profile_url(
                {"ucp-agent": 'profile="https://merchant.example/profile"'}
            )
            == "https://merchant.example/profile"
        )

    def test_uppercase_header_name(self):
        assert (
            ucp_agent_profile_url({"UCP-AGENT": 'profile="https://x.example/y"'})
            == "https://x.example/y"
        )

    def test_profile_with_other_dict_members(self):
        # RFC 8941 Dictionary members are comma-separated. `;` introduces
        # *parameters* on the preceding member, not new members.
        assert (
            ucp_agent_profile_url(
                {
                    "UCP-Agent": (
                        'profile="https://platform.example/profile",'
                        ' version="2026-04-08"'
                    )
                }
            )
            == "https://platform.example/profile"
        )

    def test_profile_after_other_member(self):
        # Order shouldn't matter; the `profile` member can appear after
        # an unrelated dictionary member, separated by a comma.
        assert (
            ucp_agent_profile_url(
                {
                    "UCP-Agent": (
                        'version="2026-04-08",'
                        ' profile="https://merchant.example/profile"'
                    )
                }
            )
            == "https://merchant.example/profile"
        )

    def test_profile_as_parameter_on_other_member_is_not_extracted(self):
        # In RFC 8941 syntax, `;profile="..."` is a parameter attached to
        # the preceding member, not a top-level member. A malformed /
        # malicious sender could otherwise smuggle an attacker-controlled
        # URI into our column via something like
        #   foo="bar";profile="https://attacker.example"
        # We must NOT extract this as a valid profile URI.
        assert (
            ucp_agent_profile_url(
                {"UCP-Agent": ('foo="bar";profile="https://attacker.example"')}
            )
            is None
        )
        assert (
            ucp_agent_profile_url(
                {
                    "UCP-Agent": (
                        'version="2026-04-08";profile="https://attacker.example"'
                    )
                }
            )
            is None
        )

    def test_no_profile_member(self):
        # Header present but missing the `profile` member (malformed,
        # non-UCP, etc.) — return None rather than misattributing.
        assert ucp_agent_profile_url({"UCP-Agent": 'version="2026-04-08"'}) is None

    def test_absent(self):
        assert ucp_agent_profile_url({"content-type": "application/json"}) is None

    def test_none(self):
        assert ucp_agent_profile_url(None) is None

    def test_empty_value(self):
        assert ucp_agent_profile_url({"UCP-Agent": ""}) is None

    def test_empty_profile_value_returns_none(self):
        # `profile=""` matches the regex but yields an empty URI which
        # isn't useful — but we don't currently special-case this; the
        # empty string lands in the column. Pin current behavior.
        assert ucp_agent_profile_url({"UCP-Agent": 'profile=""'}) is None

    def test_url_with_path_and_query(self):
        # Real profile URIs have paths and sometimes query strings.
        url = "https://platform.example/.well-known/ucp?v=2026-04-08"
        assert ucp_agent_profile_url({"UCP-Agent": f'profile="{url}"'}) == url
