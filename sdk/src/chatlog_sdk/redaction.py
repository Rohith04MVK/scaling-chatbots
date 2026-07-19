"""SDK re-export of the shared Presidio redactor.

Implementation lives in ``chatlog_pii.pii_redactor`` so the SDK and backend
share one module instead of drifting copies.
"""

from chatlog_pii.pii_redactor import redact

__all__ = ["redact"]
