"""Deterministic guardrails applied to every architecture's LLM output.

The LLM interprets and recommends; code stays authoritative for thresholds and
rule-driven facts:
  * business approvals (Manager/Department Head/Finance/CFO) come from the rules
    engine; the model may only *add* review roles (Procurement/Security/Privacy/Legal);
  * rule-decidable risk flags are set by code in both directions; the model may add
    judgment flags (e.g. existing_tool_overlap);
  * missing information about deterministic fields comes from code;
  * the next action can never be less conservative than the rules baseline;
  * evidence must reference a tool that was actually called and quote numbers that
    appear in tool outputs, otherwise it is dropped;
  * human_review_required is always True.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.contracts import EvidenceItem, ProcurementDecision, RunTelemetry
from src.policy_engine import order_approvals
from src.tools import RunContext

NEXT_ACTIONS = ["route_for_approval", "reuse_existing", "reject", "manual_review", "request_info"]
# Higher = more conservative. The model may never go below the rules baseline.
CONSERVATISM = {"route_for_approval": 0, "reuse_existing": 1, "reject": 1, "manual_review": 2, "request_info": 2}

BUSINESS_APPROVALS = {"Manager", "Department Head", "Finance", "CFO"}
REVIEW_APPROVALS = {"Procurement", "Security", "Privacy", "Legal"}
APPROVAL_SYNONYMS = [
    ("department head", "Department Head"), ("dept head", "Department Head"), ("head of", "Department Head"),
    ("director", "Department Head"), ("cfo", "CFO"), ("chief financial", "CFO"), ("finance", "Finance"),
    ("procurement", "Procurement"), ("infosec", "Security"), ("security", "Security"), ("privacy", "Privacy"),
    ("dpo", "Privacy"), ("legal", "Legal"), ("counsel", "Legal"), ("manager", "Manager"),
]

# Flags the rules engine decides on its own (set or cleared by code).
DETERMINISTIC_FLAGS = {"budget_insufficient", "vendor_risk_unavailable", "vendor_review_expired",
                       "conflicting_vendor_evidence", "security_review_required", "privacy_review_required",
                       "legal_review_required"}
FLAG_SYNONYMS = [
    ("overlap", "existing_tool_overlap"), ("existing_tool", "existing_tool_overlap"),
    ("budget", "budget_insufficient"), ("injection", "prompt_injection_detected"),
    ("conflict", "conflicting_vendor_evidence"), ("expired", "vendor_review_expired"),
    ("unavailable", "vendor_risk_unavailable"), ("missing", "missing_information"),
    ("security", "security_review_required"), ("privacy", "privacy_review_required"),
    ("legal", "legal_review_required"),
]

# Policy section 1 fields. Only "business purpose" quality is left to model judgment.
MISSING_FIELD_KEYWORDS = {
    "annual cost": ["cost", "price", "budget amount", "spend"],
    "number of users": ["user", "seat", "licen"],
    "data access level": ["data access", "data class", "data level", "data-access"],
    "required integrations": ["integration"],
    "requester / department": ["requester", "department"],
    "product / vendor": ["product", "vendor"],
    "business purpose": ["purpose", "justification", "use case", "business need"],
}


class LLMEvidence(BaseModel):
    source: str = ""
    finding: str = ""
    reference: str | None = None


class LLMDecision(BaseModel):
    """Structured output requested from the model (lenient parsing)."""
    recommendation: str = ""
    next_action: str = "manual_review"
    evidence: list[LLMEvidence] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    next_step: str = ""
    rationale: str | None = None

    @field_validator("evidence", mode="before")
    @classmethod
    def _coerce_evidence(cls, v: Any) -> Any:
        if not isinstance(v, list):
            return []
        return [x if isinstance(x, dict) else {"source": "", "finding": str(x)} for x in v]

    @field_validator("required_approvals", "missing_information", "risk_flags", mode="before")
    @classmethod
    def _coerce_list(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return [str(x) if not isinstance(x, str) else x for x in v]


LLM_DECISION_JSON_SPEC = """Return ONLY a JSON object with exactly these keys:
{
  "recommendation": "one or two sentences - advisory, never an approval",
  "next_action": "route_for_approval | request_info | reuse_existing | manual_review | reject",
  "evidence": [{"source": "<exact tool name you called>", "finding": "<fact copied from that tool result>", "reference": "<record id / policy section / endpoint>"}],
  "required_approvals": ["Manager | Department Head | Procurement | Finance | CFO | Security | Privacy | Legal"],
  "missing_information": ["<policy-required field that is missing or not credible>"],
  "risk_flags": ["existing_tool_overlap | budget_insufficient | security_review_required | privacy_review_required | legal_review_required | vendor_review_expired | conflicting_vendor_evidence | vendor_risk_unavailable | prompt_injection_detected | missing_information"],
  "next_step": "the concrete next human action",
  "rationale": "short reasoning"
}"""


# ---------------------------------------------------------------- normalisation helpers

def normalize_approval(text: str) -> str | None:
    low = text.lower()
    for key, canonical in APPROVAL_SYNONYMS:
        if key in low:
            return canonical
    return None


def normalize_flag(text: str) -> str:
    low = re.sub(r"[\s\-]+", "_", text.strip().lower())
    known = {"existing_tool_overlap", "budget_insufficient", "security_review_required", "privacy_review_required",
             "legal_review_required", "vendor_review_expired", "conflicting_vendor_evidence",
             "vendor_risk_unavailable", "prompt_injection_detected", "missing_information"}
    if low in known:
        return low
    for key, canonical in FLAG_SYNONYMS:
        if key in low:
            return canonical
    return low


def normalize_action(text: str) -> str:
    low = re.sub(r"[\s\-]+", "_", (text or "").strip().lower())
    if low in CONSERVATISM:
        return low
    for key, action in [("info", "request_info"), ("clarif", "request_info"), ("manual", "manual_review"),
                        ("escalat", "manual_review"), ("reuse", "reuse_existing"), ("existing", "reuse_existing"),
                        ("reject", "reject"), ("deny", "reject"), ("route", "route_for_approval"),
                        ("approv", "route_for_approval")]:
        if key in low:
            return action
    return "manual_review"


def _missing_category(item: str) -> str | None:
    low = item.lower()
    for category, keywords in MISSING_FIELD_KEYWORDS.items():
        if any(k in low for k in keywords):
            return category
    return None


# Wording that would imply the copilot itself approved or bought something.
AUTONOMY_RE = re.compile(r"\b(request|purchase|spend) (is|has been) approved\b|\bapprove (it|this|the request) "
                         r"(now|immediately)\b|\b(i|we) (have )?(approved|purchased)\b|\bpurchase now\b", re.I)

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers_grounded(finding: str, corpus: str) -> bool:
    for raw in _NUM_RE.findall(finding):
        num = raw.replace(",", "")
        if len(num.replace(".", "")) < 2:
            continue  # ignore single digits
        candidates = {num}
        try:
            f = float(num)
            candidates |= {str(int(f)), f"{f:.1f}", f"{f}"}
        except ValueError:
            pass
        if not any(c in corpus for c in candidates):
            return False
    return True


def ground_evidence(items: list[LLMEvidence], ctx: RunContext) -> tuple[list[EvidenceItem], int]:
    called = ctx.called_tool_names()
    # Strip thousands separators so "$10,000" in a finding matches "$10,000" or 10000 in tool output.
    corpus = re.sub(r"(?<=\d),(?=\d)", "", ctx.tool_results_text())
    kept: list[EvidenceItem] = []
    dropped = 0
    for item in items:
        source = item.source.strip()
        if source not in called or not item.finding.strip() or not _numbers_grounded(item.finding, corpus):
            dropped += 1
            continue
        kept.append(EvidenceItem(source=source, finding=item.finding.strip(), reference=item.reference))
    return kept, dropped


# ---------------------------------------------------------------- deterministic evidence + templates

def rules_evidence(rules: dict) -> list[EvidenceItem]:
    src = "run_policy_checks"
    ev: list[EvidenceItem] = []
    tier = rules["approval_tier"]
    ev.append(EvidenceItem(source=src, finding=f"Approval tier {tier['tier']}: {tier['reason']} -> minimum approvals "
                           f"{', '.join(tier['approvals'])}", reference="Policy section 4"))
    budget = rules["budget"]
    ev.append(EvidenceItem(source=src, finding=f"Budget ({rules.get('department') or 'unknown department'}): "
                           f"{budget['reason']}", reference=f"department_budgets.csv/{rules.get('department')}; Policy section 2"))
    v = rules["vendor"]
    if v["risk_service_ok"]:
        ev.append(EvidenceItem(source=src, finding=(
            f"Vendor-risk service: status '{v.get('api_security_status')}', last review {v.get('api_review_date')}, "
            f"risk {v.get('risk_level')}, personal data={v.get('processes_personal_data')}, "
            f"outside region={v.get('stores_data_outside_region')}; registry: '{v.get('registry_security_status')}' "
            f"({v.get('registry_review_date')}), new vendor={v['new_vendor']}, legal terms '{v.get('legal_terms_status')}'"),
            reference="vendor-risk API + vendors.csv; Policy section 5"))
    for note in v["notes"]:
        ev.append(EvidenceItem(source=src, finding=note, reference="Policy sections 5, 10"))
    for t in rules["review_triggers"]:
        ev.append(EvidenceItem(source=src, finding=f"{t['review']} review required: {t['reason']}",
                               reference=t["policy_section"]))
    matches = [m for m in rules["existing_tool_matches"] if {"same product", "same category"} & set(m["match_reasons"])]
    if matches:
        desc = "; ".join(f"{m['product_name']} ({m['category']}, {m['licensed_seats']} seats, scope {m['scope']})"
                         for m in matches)
        ev.append(EvidenceItem(source=src, finding=f"Existing approved software overlaps: {desc}",
                               reference="software_catalog.csv; Policy section 3"))
    if rules["missing_information"]:
        ev.append(EvidenceItem(source=src, finding="Missing required information: " + ", ".join(rules["missing_information"]),
                               reference="Policy section 1"))
    for hit in rules["prompt_injection"]:
        ev.append(EvidenceItem(source=src, finding=f"Instruction-like text in {hit['field']} ignored: "
                               f"\"{hit['matched_text']}\"", reference="Policy section 9"))
    return ev


def template_texts(action: str, approvals: list[str], rules: dict) -> tuple[str, str]:
    reviewers = ", ".join(approvals) or "Procurement"
    if action == "request_info":
        missing = ", ".join(rules["missing_information"]) or "the missing details"
        return ("Request more information before review - the request is incomplete.",
                f"Return the request to the requester to supply: {missing}. Re-run the review once complete.")
    if action == "manual_review":
        reasons = [f for f in rules["risk_flags"] if f in {"vendor_risk_unavailable", "conflicting_vendor_evidence",
                                                            "prompt_injection_detected", "vendor_review_expired"}]
        return ("Escalate to manual review - material evidence is unavailable, conflicting or untrusted.",
                f"Route to {reviewers} for manual review ({', '.join(reasons) or 'unverified evidence'}). "
                "Do not proceed with purchase until resolved.")
    if action == "reuse_existing":
        return ("Check existing approved tools before purchasing - overlap found.",
                f"Procurement to confirm with the requester whether existing licences meet the need; "
                f"if a credible gap remains, route to {reviewers}.")
    if action == "reject":
        return ("Recommend not proceeding with this request.",
                f"Procurement to confirm the recommendation with {reviewers} and inform the requester.")
    return ("Route for the required human approvals.",
            f"Send the evidence package to {reviewers} for approval. The copilot does not approve or purchase.")


# ---------------------------------------------------------------- final merge

def finalize(ctx: RunContext, llm: LLMDecision | None, *, architecture: str, model: str | None,
             llm_error: str | None = None) -> ProcurementDecision:
    corrections: list[str] = []
    if "run_policy_checks" not in ctx.called_tool_names():
        ctx.call("run_policy_checks", {})  # guardrail always runs the deterministic engine
        corrections.append("guardrail ran run_policy_checks (model did not call it)")
    rules = ctx.rules()
    rule_approvals = set(rules["required_approvals"])
    rule_flags = set(rules["risk_flags"])
    baseline = rules["baseline_next_action"]

    if llm is None:
        # Rules-only fallback: never favourable, always human review.
        action = baseline if baseline != "route_for_approval" else "manual_review"
        approvals = order_approvals(list(rule_approvals) + ["Procurement"])
        flags = list(dict.fromkeys(rules["risk_flags"] + ["llm_unavailable"]))
        recommendation, next_step = template_texts(action, approvals, rules)
        recommendation = "AI analysis unavailable - deterministic checks only. " + recommendation
        return ProcurementDecision(
            request_id=str(ctx.request.get("request_id")), recommendation=recommendation,
            evidence=rules_evidence(rules), required_approvals=approvals,
            missing_information=list(rules["missing_information"]), risk_flags=flags, next_step=next_step,
            human_review_required=True, next_action=action,
            telemetry=_telemetry(ctx, architecture, model, ["rules-only fallback"], 0, llm_error))

    # Approvals: business tiers are code-only; model may add review roles.
    approvals = set(rule_approvals)
    for raw in llm.required_approvals:
        canonical = normalize_approval(raw)
        if canonical is None:
            continue
        if canonical in rule_approvals:
            continue
        if canonical in REVIEW_APPROVALS:
            approvals.add(canonical)
            corrections.append(f"kept model-added review: {canonical}")
        else:
            corrections.append(f"removed model approval not required by thresholds: {canonical}")
    for missing_role in rule_approvals - {normalize_approval(a) for a in llm.required_approvals}:
        corrections.append(f"added rule-required approval: {missing_role}")

    # Flags: deterministic flags follow the rules; judgment flags may be added.
    flags = list(rules["risk_flags"])
    model_flags = {normalize_flag(f) for f in llm.risk_flags}
    for f in sorted(model_flags):
        if f in rule_flags:
            continue
        if f in DETERMINISTIC_FLAGS:
            corrections.append(f"removed model flag contradicted by rules: {f}")
        else:
            flags.append(f)
    for f in sorted(rule_flags - model_flags):
        corrections.append(f"added rule flag: {f}")

    # Missing information: deterministic fields from code; model may judge business purpose.
    missing = list(rules["missing_information"])
    rule_cats = {_missing_category(m) for m in missing}
    for item in llm.missing_information:
        cat = _missing_category(item)
        if cat == "business purpose" and cat not in rule_cats:
            missing.append(item.strip())
            rule_cats.add(cat)
        elif cat not in rule_cats:
            corrections.append(f"dropped model missing-info not supported by request data: {item[:60]}")
    if missing and "missing_information" not in flags:
        flags.append("missing_information")

    # Next action can never be less conservative than the rules baseline.
    action = normalize_action(llm.next_action)
    if CONSERVATISM[action] < CONSERVATISM[baseline]:
        corrections.append(f"next_action {action} overridden to rules baseline {baseline}")
        action = baseline
    if missing and action not in {"request_info", "manual_review"}:
        corrections.append(f"next_action {action} overridden to request_info (missing information)")
        action = "request_info"

    approvals_list = order_approvals(list(approvals))
    recommendation, next_step = llm.recommendation.strip(), llm.next_step.strip()
    if any(c.startswith("next_action") for c in corrections) or not recommendation or not next_step:
        recommendation, next_step = template_texts(action, approvals_list, rules)
    if AUTONOMY_RE.search(recommendation):
        recommendation = template_texts(action, approvals_list, rules)[0]
        corrections.append("rewrote recommendation that implied autonomous approval")

    evidence, dropped = ground_evidence(llm.evidence, ctx)
    if dropped:
        corrections.append(f"dropped {dropped} ungrounded evidence item(s)")
    seen = {e.finding for e in evidence}
    evidence += [e for e in rules_evidence(rules) if e.finding not in seen]

    return ProcurementDecision(
        request_id=str(ctx.request.get("request_id")), recommendation=recommendation, evidence=evidence,
        required_approvals=approvals_list, missing_information=missing, risk_flags=list(dict.fromkeys(flags)),
        next_step=next_step, human_review_required=True, next_action=action,
        telemetry=_telemetry(ctx, architecture, model, corrections, dropped, llm_error))


def _telemetry(ctx: RunContext, architecture: str, model: str | None, corrections: list[str],
               dropped: int, llm_error: str | None) -> RunTelemetry:
    t = ctx.telemetry
    return RunTelemetry(llm_calls=t.llm_calls, tool_calls=t.tool_calls, tool_names=list(t.tool_names),
                        architecture=architecture, model=model, prompt_tokens=t.prompt_tokens,
                        completion_tokens=t.completion_tokens, llm_latency_ms=round(t.llm_latency_ms, 1),
                        cached_llm_calls=t.cached_llm_calls,
                        guardrail_corrections=corrections, ungrounded_evidence_dropped=dropped, llm_error=llm_error)
