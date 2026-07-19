import pytest
from chatlog.services.redaction import redact


@pytest.mark.parametrize(
    ("raw", "sensitive_value", "marker"),
    [
        ("Contact Alice at alice@example.com.", "alice@example.com", "<EMAIL_ADDRESS>"),
        ("Call +1 (415) 555-2671 today.", "+1 (415) 555-2671", "<PHONE_NUMBER>"),
        ("SSN: 457-55-5462", "457-55-5462", "<US_SSN>"),
        ("Patient Alice Johnson checked in.", "Alice Johnson", "<PERSON>"),
    ],
)
def test_redact_replaces_supported_pii(raw: str, sensitive_value: str, marker: str) -> None:
    result = redact(raw)

    assert marker in result
    assert sensitive_value not in result


def test_redact_replaces_multiple_pii_values() -> None:
    result = redact("Email me@example.com or call 212-555-0198; SSN 457-55-5462.")

    assert "me@example.com" not in result
    assert "212-555-0198" not in result
    assert "457-55-5462" not in result
    assert "<EMAIL_ADDRESS>" in result
    assert "<PHONE_NUMBER>" in result
    assert "<US_SSN>" in result


def test_redact_leaves_non_pii_text_unchanged() -> None:
    value = "The request completed in 420 ms with 128 tokens."

    assert redact(value) == value
