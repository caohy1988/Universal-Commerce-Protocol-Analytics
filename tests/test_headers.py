"""Direct unit coverage for the _headers helper.

Lives in its own file (no Starlette dependency) so the helper is
exercised even in dev-only environments where the optional [fastapi]
extra isn't installed.
"""

from __future__ import annotations

from ucp_analytics._headers import is_signed, lookup_header, signature_keyid


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
