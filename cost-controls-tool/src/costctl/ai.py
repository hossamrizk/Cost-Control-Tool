"""AI interpretation layer.

Division of labour, and the reason this module is small:

  the engine  decides *what* is wrong and by how much  (arithmetic)
  this module decides *how to say it* and *what to do* (language)

The model never sees raw source files and never performs arithmetic. It receives
a finding whose figures are already computed and returns prose plus a proposed
severity and confidence, which are recorded *alongside* the deterministic values
rather than replacing them. A numeric guardrail then rejects any response that
introduces a monetary figure the engine did not compute, which is the enforcement
mechanism for BR-01.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .models import AIInterpretation, Finding

PROMPT_VERSION = "p3-2026-07-30"
DEFAULT_MODEL = "gemini-2.5-flash"

SYSTEM_INSTRUCTION = """\
You are a senior project controls reviewer commenting on cost findings that have
already been calculated by a deterministic engine.

Hard rules:
1. Never perform arithmetic. Never derive, estimate, restate or infer any figure.
2. Only reference monetary amounts that appear verbatim in the FACTS you are given.
   If a number is not in the FACTS, do not mention any number in its place.
3. Never assert that a figure is correct or incorrect beyond what the FACTS state.
4. Distinguish a confirmed error (the arithmetic disagrees with the definition)
   from an item that requires explanation (the arithmetic holds but the result is
   unusual or unsupported).
5. Write for a project director. Plain, specific, no filler, no apology.

Return JSON only, with exactly these keys:
  explanation          2-4 sentences: what this means for the project and why it matters.
  recommended_review   one sentence: the next investigative step.
  recommended_action   one sentence: the action to take.
  proposed_severity    one of Low, Medium, High, Critical.
  proposed_confidence  integer 0-100, your confidence that this is a real issue.
  severity_rationale   one sentence justifying the proposed severity.
"""

# Monetary tokens: $1,234,567 | $1.5M | $1.5 million | 1,234,567
_MONEY = re.compile(
    r"\$?\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?(million|m|bn|billion|k)?\b",
    re.IGNORECASE)
_MULTIPLIER = {"m": Decimal("1000000"), "million": Decimal("1000000"),
               "bn": Decimal("1000000000"), "billion": Decimal("1000000000"),
               "k": Decimal("1000")}


def _to_dollars(number: str, suffix: str | None) -> Decimal | None:
    try:
        value = Decimal(number.replace(",", ""))
    except InvalidOperation:
        return None
    if suffix:
        value *= _MULTIPLIER[suffix.lower()]
    elif "," not in number:
        # A bare ungrouped number is not treated as money (cost codes, row
        # numbers, percentages, years, counts).
        return None
    return value


def _allowed_values(finding: Finding) -> set[Decimal]:
    allowed: set[Decimal] = set()

    def add(raw):
        if raw is None:
            return
        try:
            allowed.add(abs(Decimal(str(raw))))
        except (InvalidOperation, ValueError):
            return

    for value in (finding.reported_value, finding.calculated_value,
                  finding.difference, finding.potential_exposure):
        add(value)
    for value in finding.evidence.values():
        if isinstance(value, (str, int, float, Decimal)):
            add(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for inner in item.values():
                        add(inner)
                else:
                    add(item)
    # Amounts already stated in the deterministic description are, by definition,
    # engine-computed and safe to echo.
    for match in _MONEY.finditer(finding.finding_description):
        dollars = _to_dollars(match.group(1), match.group(2))
        if dollars is not None:
            allowed.add(abs(dollars))
    return {v for v in allowed if v != 0}


def check_numeric_guardrail(finding: Finding, text: str) -> tuple[bool, str]:
    """Reject any monetary figure the deterministic engine did not produce."""
    allowed = _allowed_values(finding)
    offenders: list[str] = []
    for match in _MONEY.finditer(text or ""):
        dollars = _to_dollars(match.group(1), match.group(2))
        if dollars is None:
            continue
        dollars = abs(dollars)
        if any(abs(dollars - candidate) <= max(Decimal("1"), candidate * Decimal("0.005"))
               for candidate in allowed):
            continue
        offenders.append(match.group(0).strip())
    if offenders:
        return False, "unverified monetary figures: " + ", ".join(sorted(set(offenders)))
    return True, "all monetary figures trace to deterministic values"


def _facts(finding: Finding) -> dict:
    """Exactly what the model is allowed to know."""
    return {
        "rule_id": finding.rule_id,
        "cost_code": finding.cost_code,
        "package": finding.package,
        "category": finding.finding_category.value,
        "finding_type": finding.finding_type.value,
        "deterministic_description": finding.finding_description,
        "reported_value": str(finding.reported_value) if finding.reported_value is not None else None,
        "calculated_value": str(finding.calculated_value) if finding.calculated_value is not None else None,
        "difference": str(finding.difference) if finding.difference is not None else None,
        "potential_exposure": str(finding.potential_exposure),
        "engine_severity": finding.severity.value,
        "source_file": finding.source_file,
        "source_reference": finding.source_reference,
        "supporting_evidence": {k: v for k, v in finding.evidence.items()
                                if v is not None},
    }


class Interpreter:
    """Base interpreter. Subclasses provide _generate()."""
    provider = "base"
    model = ""

    def _generate(self, facts: dict) -> str:      # pragma: no cover - interface
        raise NotImplementedError

    def interpret(self, finding: Finding) -> AIInterpretation:
        facts = _facts(finding)
        try:
            raw = self._generate(facts)
        except Exception as exc:                   # network, auth, quota, parse
            return AIInterpretation(
                explanation="", recommended_review=finding.recommended_review,
                recommended_action=finding.recommended_action,
                provider=self.provider, model=self.model,
                prompt_version=PROMPT_VERSION, guardrail="error",
                guardrail_detail=f"{type(exc).__name__}: {exc}")

        try:
            payload = json.loads(_strip_fences(raw))
        except json.JSONDecodeError as exc:
            return AIInterpretation(
                recommended_review=finding.recommended_review,
                recommended_action=finding.recommended_action,
                provider=self.provider, model=self.model,
                prompt_version=PROMPT_VERSION, guardrail="error",
                guardrail_detail=f"model did not return valid JSON: {exc}")

        combined = " ".join(str(payload.get(k, "")) for k in
                            ("explanation", "recommended_review",
                             "recommended_action", "severity_rationale"))
        passed, detail = check_numeric_guardrail(finding, combined)

        interpretation = AIInterpretation(
            explanation=str(payload.get("explanation", "")).strip(),
            recommended_review=str(payload.get("recommended_review", "")).strip()
                               or finding.recommended_review,
            recommended_action=str(payload.get("recommended_action", "")).strip()
                               or finding.recommended_action,
            proposed_severity=(str(payload["proposed_severity"]).strip()
                               if payload.get("proposed_severity") else None),
            proposed_confidence=_as_int(payload.get("proposed_confidence")),
            severity_rationale=str(payload.get("severity_rationale", "")).strip(),
            provider=self.provider, model=self.model, prompt_version=PROMPT_VERSION,
            guardrail="passed" if passed else "blocked_numeric",
            guardrail_detail=detail)

        if not passed:
            # Blocked output is discarded, not shown. The deterministic text stands.
            interpretation.explanation = (
                "AI explanation withheld: the generated text contained monetary "
                "figures that do not trace to a deterministic calculation.")
            interpretation.recommended_review = finding.recommended_review
            interpretation.recommended_action = finding.recommended_action
            interpretation.proposed_severity = None
            interpretation.proposed_confidence = None
            interpretation.severity_rationale = ""
        return interpretation


class GeminiInterpreter(Interpreter):
    """Google Gemini via the google-genai SDK."""
    provider = "gemini"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        self.api_key = (api_key or os.environ.get("GEMINI_API_KEY")
                        or os.environ.get("GOOGLE_API_KEY"))
        if not self.api_key:
            raise RuntimeError(
                "no Gemini API key found; set GEMINI_API_KEY or run with the "
                "deterministic interpreter")
        from google import genai                       # imported lazily
        from google.genai import types
        self._types = types
        self._client = genai.Client(api_key=self.api_key)

    def _generate(self, facts: dict) -> str:
        response = self._client.models.generate_content(
            model=self.model,
            contents="FACTS:\n" + json.dumps(facts, indent=2),
            config=self._types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        return response.text or ""


class DeterministicInterpreter(Interpreter):
    """Offline fallback.

    Produces the same schema from templates so the prototype is fully
    demonstrable with no API key and so the test suite never depends on a
    network call. It is labelled as such in the output and in the UI: nothing
    here pretends to be model output.
    """
    provider = "deterministic-fallback"
    model = "template"

    def _generate(self, facts: dict) -> str:
        confirmed = facts["finding_type"].lower().startswith("confirmed")
        lead = ("This is a confirmed arithmetic or control error: "
                if confirmed else "This requires explanation rather than correction: ")
        return json.dumps({
            "explanation": lead + facts["deterministic_description"],
            "recommended_review": "",
            "recommended_action": "",
            "proposed_severity": facts["engine_severity"],
            "proposed_confidence": 100 if confirmed else 80,
            "severity_rationale": f"Severity carried from rule {facts['rule_id']} "
                                  f"priority and exposure materiality.",
        })


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _as_int(value) -> int | None:
    try:
        return max(0, min(100, int(float(value))))
    except (TypeError, ValueError):
        return None


def build_interpreter(kind: str = "auto") -> Interpreter:
    """kind: gemini | deterministic | auto (gemini if a key is present)."""
    if kind == "deterministic":
        return DeterministicInterpreter()
    if kind == "gemini":
        return GeminiInterpreter()
    try:
        return GeminiInterpreter()
    except Exception:
        return DeterministicInterpreter()
