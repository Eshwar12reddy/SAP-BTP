"""Checks on model output before it is used or stored.

Structured output: ``parse_structured`` turns model JSON into a strict Pydantic contract
(unknown fields rejected, enums and bounds validated). An invalid response is rejected,
never repaired by guessing. A valid object is still only a proposal: nothing is authorized
or executed because the model produced it.

Free text: ``check_answer`` masks secrets, defangs lines that imitate the backend's source
evidence, and appends a correction when the text claims a live check, an approval or a
change. Evidence (sources, live checks, timestamps) comes only from backend tool results.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError

from app.safety.redaction import Redactor

logger = logging.getLogger(__name__)

MAX_STRUCTURED_CHARS = 4_000


class StrictModelOutput(BaseModel):
    """Base for contracts parsed from model output."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=500)


class ModelOutputError(Exception):
    """The model response did not satisfy its contract. ``category`` is safe to log."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


_FENCED = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL | re.IGNORECASE)


def parse_structured[C: StrictModelOutput](text: str, contract: type[C]) -> C:
    if len(text) > MAX_STRUCTURED_CHARS:
        raise ModelOutputError("too_long")
    body = text.strip()
    fenced = _FENCED.match(body)
    if fenced:
        body = fenced.group(1).strip()
    try:
        data = json.loads(body)
    except ValueError:
        raise ModelOutputError("invalid_json") from None
    if not isinstance(data, dict):
        raise ModelOutputError("not_an_object")
    try:
        return contract.model_validate(data)
    except ValidationError:
        # The validation message can quote model text; only the category is reported.
        raise ModelOutputError("schema_violation") from None


def log_rejected_output(contract: str, category: str) -> None:
    logger.warning(
        "Model output rejected",
        extra={"event": "ai_output_validation_failed", "reason": contract, "error_code": category},
    )


# Lines a model might write to imitate the backend's evidence footer.
_EVIDENCE_LINE = re.compile(
    r"^(\s*)(?:[—–-]+\s*)?((?:source|sources|evidence|verified|verification|retrieved|"
    r"live\s+check)\s*:)",
    re.IGNORECASE | re.MULTILINE,
)
_LIVE_CLAIM = re.compile(
    r"\bI\s+(have\s+)?(checked|inspected|verified|looked\s+at|scanned|monitored|queried|"
    r"confirmed)\b|\byour\s+(apps?|applications?|services?|environments?|systems?|accounts?)"
    r"\s+(is|are)\s+(healthy|running|up|down|fine|ok)\b",
    re.IGNORECASE,
)
_CHANGE_VERBS = (
    r"(deployed|redeployed|restarted|scaled|updated|increased|decreased|deleted|executed|"
    r"applied|changed|modified|provisioned|approved)"
)
_CHANGE_CLAIM = re.compile(
    rf"\b(?:approved\b[.!]|(?:has|have|was|were)\s+been\s+{_CHANGE_VERBS}\b|"
    rf"successfully\s+{_CHANGE_VERBS}\b|I\s+(?:have\s+)?{_CHANGE_VERBS}\b)",
    re.IGNORECASE,
)
LIVE_CLAIM_NOTE = (
    "Note: this answer was written without access to your systems. Anything that sounds "
    "like a live check is not verified."
)
CHANGE_CLAIM_NOTE = (
    "Note: nothing was approved, executed or changed. This assistant cannot approve or "
    "carry out infrastructure changes."
)


@dataclass(frozen=True)
class CheckedAnswer:
    text: str
    findings: tuple[str, ...]


def check_answer(text: str, redactor: Redactor) -> CheckedAnswer:
    redaction = redactor.redact(text)
    text = redaction.text
    findings: list[str] = []
    if redaction.count:
        findings.append("secret_in_output")
    text, fake_evidence = _EVIDENCE_LINE.subn(r"\1(unverified model text) \2", text)
    if fake_evidence:
        findings.append("model_evidence_line")
    notes = []
    if _LIVE_CLAIM.search(text):
        findings.append("unverified_live_claim")
        notes.append(LIVE_CLAIM_NOTE)
    if _CHANGE_CLAIM.search(text):
        findings.append("unverified_change_claim")
        notes.append(CHANGE_CLAIM_NOTE)
    for finding in findings:
        log_rejected_output("answer_text", finding)
    if notes:
        text = text.rstrip() + "\n\n" + "\n\n".join(notes)
    return CheckedAnswer(text=text, findings=tuple(findings))
