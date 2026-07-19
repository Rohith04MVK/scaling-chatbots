"""Presidio PII redaction with a module-level AnalyzerEngine singleton.

Public contract: ``redact(text) -> str`` replaces detected entities with typed
placeholders (e.g. ``<EMAIL_ADDRESS>``). The analyzer is built once at import
time — constructing it per call is too expensive under load.
"""

from __future__ import annotations

import re

from presidio_analyzer import AnalyzerEngine, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# Healthcare-adjacent entity set. DATE_TIME is included but filtered to DOB-like
# spans so incidental phrases like "today" are not redacted.
_ENTITIES: tuple[str, ...] = (
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "US_SSN",
    "LOCATION",
    "DATE_TIME",
    "MEDICAL_LICENSE",
)

_OPERATORS: dict[str, OperatorConfig] = {
    entity: OperatorConfig("replace", {"new_value": f"<{entity}>"}) for entity in _ENTITIES
}

# Keep DATE_TIME only when the span or nearby context looks like a date of birth.
_DOB_CONTEXT = re.compile(
    r"(?i)\b(?:dob|d\.o\.b\.|date\s+of\s+birth|born(?:\s+on)?|birthday)\b"
)
_DOB_DATE = re.compile(
    r"\b(?:0?[1-9]|1[0-2])[/.-](?:0?[1-9]|[12]\d|3[01])[/.-](?:19|20)\d{2}\b"
    r"|\b(?:19|20)\d{2}[/.-](?:0?[1-9]|1[0-2])[/.-](?:0?[1-9]|[12]\d|3[01])\b"
)


def _build_analyzer() -> AnalyzerEngine:
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    )
    return AnalyzerEngine(nlp_engine=provider.create_engine())


# Construct once at module load; reuse across every redact() call.
_ANALYZER: AnalyzerEngine = _build_analyzer()
_ANONYMIZER: AnonymizerEngine = AnonymizerEngine()


def _is_dob_like(text: str, result: RecognizerResult) -> bool:
    span = text[result.start : result.end]
    if _DOB_DATE.search(span):
        return True
    window_start = max(0, result.start - 40)
    window_end = min(len(text), result.end + 10)
    return _DOB_CONTEXT.search(text[window_start:window_end]) is not None


def _filter_results(text: str, results: list[RecognizerResult]) -> list[RecognizerResult]:
    kept: list[RecognizerResult] = []
    for result in results:
        if result.entity_type == "DATE_TIME" and not _is_dob_like(text, result):
            continue
        kept.append(result)
    return kept


def redact(text: str) -> str:
    """Replace detected PII with typed placeholders; return ``text`` unchanged if empty."""
    if not text:
        return text
    results = _ANALYZER.analyze(text=text, language="en", entities=list(_ENTITIES))
    filtered = _filter_results(text, results)
    if not filtered:
        return text
    return _ANONYMIZER.anonymize(
        text=text,
        analyzer_results=filtered,
        operators=_OPERATORS,
    ).text
