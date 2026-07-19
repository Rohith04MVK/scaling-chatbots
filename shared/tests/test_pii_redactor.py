"""Unit tests for the shared Presidio ``redact()`` contract."""

import chatlog_pii.pii_redactor as pii_redactor
from chatlog_pii.pii_redactor import redact


def test_redact_email() -> None:
    raw = "Contact support at alice@example.com for help."
    result = redact(raw)

    assert "<EMAIL_ADDRESS>" in result
    assert "alice@example.com" not in result


def test_redact_phone_number() -> None:
    raw = "Call +1 (415) 555-2671 today."
    result = redact(raw)

    assert "<PHONE_NUMBER>" in result
    assert "+1 (415) 555-2671" not in result
    assert "555-2671" not in result


def test_redact_person_name() -> None:
    raw = "Patient Alice Johnson arrived for intake."
    result = redact(raw)

    assert "<PERSON>" in result
    assert "Alice Johnson" not in result


def test_redact_us_ssn() -> None:
    # Presidio rejects well-known invalid sample SSNs such as 123-45-6789.
    raw = "SSN on file: 457-55-5462"
    result = redact(raw)

    assert "<US_SSN>" in result
    assert "457-55-5462" not in result


def test_analyzer_engine_is_module_singleton() -> None:
    first = pii_redactor._ANALYZER
    redact("hello@example.com")
    redact("Call 212-555-0198")
    assert pii_redactor._ANALYZER is first
