"""Deterministic procurement rules engine.

Everything in this module is plain, side-effect-free Python. It encodes the parts
of `data/procurement_policy.md` that must never depend on an LLM: required
fields, approval thresholds, budget, security/privacy/legal triggers, review
expiry against the policy reference date, registry-vs-API conflicts, tool
failures and prompt-injection patterns.
"""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

from src.data_access import load_policy_text

DEFAULT_REFERENCE_DATE = date(2026, 9, 30)
REVIEW_VALIDITY_DAYS = 365
NEW_VENDOR_LEGAL_THRESHOLD = 10_000

# Data-access levels that trigger review (policy sections 5 and 6).
SECURITY_DATA_LEVELS = {
    "source_code": "source code access",
    "confidential_documents": "confidential documents",
    "employee_pii": "employee PII",
    "customer_pii": "customer PII",
    "credentials": "credentials/secrets",
    "secrets": "credentials/secrets",
    "production_telemetry": "production data access",
    "production_access": "production access",
}
PII_DATA_LEVELS = {"employee_pii", "customer_pii"}
SENSITIVE_DATA_LEVELS = set(SECURITY_DATA_LEVELS)
UNKNOWN_VALUES = {"", "unknown", "n/a", "na", "none_specified", "tbd", "?"}

SECURITY_INTEGRATION_PATTERNS = [
    (re.compile(r"production|prod\b", re.I), "production integration"),
    (re.compile(r"cloud[- ]?account|aws|gcp|azure", re.I), "cloud-account integration"),
    (re.compile(r"\bgit\b|repositor|source", re.I), "source-code repository integration"),
    (re.compile(r"secret|credential|vault|password", re.I), "credentials/secrets integration"),
]

INJECTION_PATTERNS = [
    # Up to three words between the verb and the target ("ignore all procurement rules").
    re.compile(r"\b(ignore|disregard|forget)\s+(\w+\s+){0,3}(rules|instructions|polic(y|ies)|controls|guidelines)", re.I),
    re.compile(r"(treat|mark|consider)\s+(this|it|the request)\s+(request\s+)?as\s+(already\s+)?(\w+[- ])?approved", re.I),
    re.compile(r"\b(cfo|ceo|vp|finance|security|legal)[- ]approved\b", re.I),
    re.compile(r"\bapprove\s+(it|this|the request)?\s*(immediately|now|right away|automatically)", re.I),
    re.compile(r"(bypass|skip|override)\s+(the\s+)?(security|privacy|legal|procurement|approval|review|policy|controls)", re.I),
    re.compile(r"(system\s+prompt|you\s+are\s+now|act\s+as\s+(the\s+)?(admin|cfo|approver))", re.I),
    re.compile(r"(reveal|expose|print|show)\s+(the\s+)?(api[- ]?key|secret|password|credentials|system prompt)", re.I),
]

APPROVAL_ORDER = ["Manager", "Department Head", "Procurement", "Finance", "CFO", "Security", "Privacy", "Legal"]


def policy_reference_date() -> date:
    """Read the reference date from the policy document; fall back to the known value."""
    try:
        text = load_policy_text()
    except OSError:
        return DEFAULT_REFERENCE_DATE
    match = re.search(r"reference date[^0-9]*(\d{4}-\d{2}-\d{2})", text, re.I)
    return date.fromisoformat(match.group(1)) if match else DEFAULT_REFERENCE_DATE


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in UNKNOWN_VALUES:
        return True
    return False


def valid_number(value: Any) -> float | None:
    """A usable cost / count: a real, finite, non-negative number (not bool, NaN or text)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def normalize_data_level(value: Any) -> str:
    """'Customer PII' / 'customer-pii' / ' customer_pii ' -> 'customer_pii'."""
    return re.sub(r"[\s\-]+", "_", str(value or "").strip().lower())


def normalize_integrations(value: Any) -> list[str] | None:
    """List of integration names; a comma-separated string is split; None or other types -> None (missing)."""
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return None


def _parse_date(value: Any) -> date | None:
    if not value or (isinstance(value, float) and value != value):  # NaN from pandas
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def order_approvals(approvals: list[str]) -> list[str]:
    unique = list(dict.fromkeys(approvals))
    return sorted(unique, key=lambda a: APPROVAL_ORDER.index(a) if a in APPROVAL_ORDER else len(APPROVAL_ORDER))


# ---------------------------------------------------------------- individual checks

def check_required_fields(request: dict, department: str | None) -> list[str]:
    """Policy section 1: list material information that is missing."""
    missing: list[str] = []
    if _is_missing(request.get("requester_id")) or not department:
        missing.append("requester / department")
    if _is_missing(request.get("product_name")) or _is_missing(request.get("vendor_name")):
        missing.append("product / vendor")
    if valid_number(request.get("annual_cost_usd")) is None:
        missing.append("annual cost (or a reasonable annual estimate)")
    users = valid_number(request.get("user_count"))
    if users is None or users <= 0:
        missing.append("number of users / licenses")
    if _is_missing(request.get("business_justification")):
        missing.append("business purpose")
    if _is_missing(request.get("data_access_level")):
        missing.append("intended data access level")
    if normalize_integrations(request.get("requested_integrations")) is None:
        missing.append("required integrations")
    return missing


def approval_tier(annual_cost: float | None) -> dict:
    """Policy section 4: deterministic minimum business approvals."""
    if annual_cost is None:
        return {"tier": "unknown", "approvals": ["Procurement"],
                "reason": "Annual cost missing - approval tier cannot be determined; Procurement must triage."}
    if annual_cost <= 1_000:
        return {"tier": "<= $1,000", "approvals": ["Manager"], "reason": f"${annual_cost:,.2f} is up to $1,000"}
    if annual_cost <= 10_000:
        return {"tier": "$1,000.01 - $10,000", "approvals": ["Department Head", "Procurement"],
                "reason": f"${annual_cost:,.2f} is between $1,000.01 and $10,000"}
    if annual_cost <= 25_000:
        return {"tier": "$10,000.01 - $25,000", "approvals": ["Department Head", "Finance", "Procurement"],
                "reason": f"${annual_cost:,.2f} is between $10,000.01 and $25,000"}
    return {"tier": "> $25,000", "approvals": ["Department Head", "Finance", "CFO", "Procurement"],
            "reason": f"${annual_cost:,.2f} is above $25,000"}


def check_budget(annual_cost: float | None, budget_row: dict | None) -> dict:
    """Policy section 2: compare cost with the department's available budget."""
    if budget_row is None:
        return {"status": "unknown", "within_budget": None, "available_usd": None,
                "reason": "No budget record for the requester's department"}
    available = float(budget_row["available_usd"])
    if annual_cost is None:
        return {"status": "unknown", "within_budget": None, "available_usd": available,
                "reason": "Annual cost missing - budget cannot be checked"}
    within = annual_cost <= available
    return {
        "status": "within_budget" if within else "insufficient",
        "within_budget": within,
        "available_usd": available,
        "requested_usd": annual_cost,
        "shortfall_usd": 0.0 if within else round(annual_cost - available, 2),
        "reason": (f"${annual_cost:,.0f} requested vs ${available:,.0f} available"
                   + ("" if within else f" (shortfall ${annual_cost - available:,.0f})")),
    }


def assess_vendor(registry: dict | None, risk: dict, ref_date: date) -> dict:
    """Policy section 5: review currency, conflicts and service availability.

    `risk` is the structured result of the vendor-risk tool: {"ok": bool, "data": {...}} or
    {"ok": False, "error": ..., "status": ...}.
    """
    out: dict[str, Any] = {"registry_found": registry is not None, "risk_service_ok": bool(risk.get("ok")),
                           "flags": [], "notes": []}

    reg_status = (registry or {}).get("security_status")
    reg_date = _parse_date((registry or {}).get("security_review_date"))
    reg_current = (str(reg_status).lower() == "approved" and reg_date is not None
                   and (ref_date - reg_date).days <= REVIEW_VALIDITY_DAYS)
    out["registry_security_status"] = reg_status
    out["registry_review_date"] = reg_date.isoformat() if reg_date else None
    out["registry_review_age_days"] = (ref_date - reg_date).days if reg_date else None
    out["registry_review_current"] = reg_current
    out["new_vendor"] = registry is None or str(registry.get("procurement_status", "")).lower() != "approved"
    out["legal_terms_status"] = (registry or {}).get("legal_terms_status")

    if not risk.get("ok"):
        out["flags"].append("vendor_risk_unavailable")
        out["notes"].append(f"Vendor-risk service could not verify status ({risk.get('status')}: {risk.get('error')})")
        out["api_review_current"] = None
        # Do not infer a favourable status (policy section 10).
        out["review_current"] = False
        out["processes_personal_data"] = None
        out["stores_data_outside_region"] = None
        if registry is not None and reg_status:
            out["notes"].append(f"Internal registry says security status '{reg_status}' "
                                f"(review date {out['registry_review_date'] or 'none'}) - unverified")
        return out

    data = risk["data"]
    api_status = str(data.get("security_review_status") or "").lower()
    api_date = _parse_date(data.get("last_review_date"))
    api_current = (api_status == "approved" and api_date is not None
                   and (ref_date - api_date).days <= REVIEW_VALIDITY_DAYS)
    out.update({
        "api_security_status": api_status,
        "api_review_date": api_date.isoformat() if api_date else None,
        "api_review_age_days": (ref_date - api_date).days if api_date else None,
        "api_review_current": api_current,
        "risk_level": data.get("risk_level"),
        "processes_personal_data": data.get("processes_personal_data"),
        "stores_data_outside_region": data.get("stores_data_outside_region"),
    })
    out["review_current"] = bool(api_current and (registry is None or reg_current))

    if api_status in {"expired"} or (api_date and (ref_date - api_date).days > REVIEW_VALIDITY_DAYS) \
            or (reg_date and (ref_date - reg_date).days > REVIEW_VALIDITY_DAYS):
        out["flags"].append("vendor_review_expired")
    if api_status in {"not_completed", "pending", "missing", ""} or api_date is None:
        out["notes"].append(f"Vendor security assessment is '{api_status or 'missing'}'")

    if registry is not None:
        def bucket(status: str | None) -> str:
            s = str(status or "").strip().lower()
            return "approved" if s == "approved" else "not_approved"
        status_conflict = bucket(reg_status) != bucket(api_status)
        date_conflict = reg_date is not None and api_date is not None and reg_date != api_date
        if status_conflict or date_conflict:
            out["flags"].append("conflicting_vendor_evidence")
            out["notes"].append(
                f"Registry says '{reg_status}' ({out['registry_review_date']}) but vendor-risk service says "
                f"'{api_status}' ({out['api_review_date']}) - conflict must be resolved by Security, not silently")
    return out


def detect_prompt_injection(texts: dict[str, Any]) -> list[dict]:
    """Policy section 9: find instruction-like content inside business data."""
    hits: list[dict] = []
    for field, value in texts.items():
        if isinstance(value, (list, tuple)):
            value = " | ".join(str(v) for v in value)
        if not isinstance(value, str):
            continue
        for pattern in INJECTION_PATTERNS:
            match = pattern.search(value)
            if match:
                hits.append({"field": field, "matched_text": match.group(0)})
                break
    return hits


def find_overlap(request: dict, catalog: list[dict], department: str | None) -> list[dict]:
    """Policy section 3: existing products with the same product, vendor or category."""
    product = str(request.get("product_name") or "").lower()
    vendor = str(request.get("vendor_name") or "").lower()
    category = str(request.get("category") or "").lower()
    matches = []
    for item in catalog:
        reasons = []
        if product and str(item["product_name"]).lower() == product:
            reasons.append("same product")
        if vendor and str(item["vendor_name"]).lower() == vendor:
            reasons.append("same vendor")
        if category and str(item["category"]).lower() == category:
            reasons.append("same category")
        if reasons:
            scope = str(item.get("scope", ""))
            matches.append({
                "software_id": item["software_id"], "product_name": item["product_name"],
                "vendor_name": item["vendor_name"], "category": item["category"], "status": item["status"],
                "licensed_seats": item.get("licensed_seats"), "scope": scope, "notes": item.get("notes"),
                "match_reasons": reasons,
                "available_to_requester": scope.lower() == "company-wide" or scope.lower() == str(department or "").lower(),
            })
    return matches


# ---------------------------------------------------------------- full evaluation

def evaluate_request(
    request: dict,
    *,
    employee: dict | None,
    department_head: str | None,
    budget_row: dict | None,
    catalog: list[dict],
    registry: dict | None,
    vendor_risk: dict,
    ref_date: date | None = None,
) -> dict:
    """Run every deterministic check and return the authoritative rule result."""
    ref_date = ref_date or policy_reference_date()
    department = (employee or {}).get("department")
    cost = valid_number(request.get("annual_cost_usd"))
    data_level = normalize_data_level(request.get("data_access_level"))
    integrations = normalize_integrations(request.get("requested_integrations")) or []

    missing = check_required_fields(request, department)
    tier = approval_tier(cost)
    budget = check_budget(cost, budget_row)
    vendor = assess_vendor(registry, vendor_risk, ref_date)
    overlap = find_overlap(request, catalog, department)

    injection_texts = {f"request.{k}": v for k, v in request.items() if isinstance(v, (str, list, tuple))}
    if registry:
        injection_texts["vendor_registry.notes"] = registry.get("notes")
    if vendor_risk.get("ok"):
        injection_texts["vendor_risk.notes"] = vendor_risk["data"].get("notes")
    injection = detect_prompt_injection(injection_texts)

    approvals = list(tier["approvals"])
    flags: list[str] = []
    triggers: list[dict] = []

    def trigger(review: str, reason: str, section: str) -> None:
        triggers.append({"review": review, "reason": reason, "policy_section": section})

    # Budget (section 2)
    if budget["status"] == "insufficient":
        flags.append("budget_insufficient")
        approvals.append("Finance")
        trigger("Finance", f"Budget exception: {budget['reason']}", "Policy section 2")
    elif budget_row is None and department:
        # Section 10: an unavailable data source must not be read as a favourable status.
        flags.append("budget_unverified")
        trigger("Finance", f"No budget record for department '{department}' - budget cannot be verified",
                "Policy sections 2, 10")

    # Security (section 5)
    if data_level in SECURITY_DATA_LEVELS:
        trigger("Security", f"Data access level '{data_level}' ({SECURITY_DATA_LEVELS[data_level]})", "Policy section 5")
    for integ in integrations:
        for pattern, label in SECURITY_INTEGRATION_PATTERNS:
            if pattern.search(str(integ)):
                trigger("Security", f"Integration '{integ}' ({label})", "Policy section 5")
                break
    if not vendor["risk_service_ok"]:
        trigger("Security", "Vendor security status could not be verified (service unavailable)", "Policy sections 5, 10")
    elif not vendor["api_review_current"]:
        trigger("Security", f"Vendor security assessment is not current "
                            f"(status '{vendor.get('api_security_status')}', review date {vendor.get('api_review_date')})",
                "Policy section 5")
    elif registry is not None and not vendor["registry_review_current"]:
        trigger("Security", "Internal registry shows no current security review", "Policy section 5")
    if "conflicting_vendor_evidence" in vendor["flags"]:
        trigger("Security", "Registry and vendor-risk service disagree", "Policy section 5")

    # Privacy (section 6)
    outside = vendor.get("stores_data_outside_region")
    if data_level in PII_DATA_LEVELS:
        trigger("Privacy", f"Tool will process {SECURITY_DATA_LEVELS[data_level]}", "Policy section 6")
    if outside and (data_level in SENSITIVE_DATA_LEVELS):
        trigger("Privacy", "Vendor stores data outside the operating region and data is sensitive", "Policy section 6")

    # Legal (section 7)
    if vendor["new_vendor"] and cost is not None and cost >= NEW_VENDOR_LEGAL_THRESHOLD:
        trigger("Legal", f"New vendor with annual spend ${cost:,.0f} (>= $10,000)", "Policy section 7")
    legal_terms = str(vendor.get("legal_terms_status") or "").lower()
    if legal_terms != "approved":
        trigger("Legal", f"Legal terms not approved/standard (status '{vendor.get('legal_terms_status') or 'unknown'}')",
                "Policy section 7")
    if outside and data_level in PII_DATA_LEVELS:
        trigger("Legal", "Cross-region processing of personal data", "Policy section 7")

    for t in triggers:
        approvals.append(t["review"])
    reviews = {t["review"] for t in triggers}
    if "Security" in reviews:
        flags.append("security_review_required")
    if "Privacy" in reviews:
        flags.append("privacy_review_required")
    if "Legal" in reviews:
        flags.append("legal_review_required")
    flags.extend(vendor["flags"])
    # A shared vendor alone is an existing relationship, not functional overlap.
    if any({"same product", "same category"} & set(m["match_reasons"]) for m in overlap):
        flags.append("existing_tool_overlap")
    if missing:
        flags.append("missing_information")
    if injection:
        flags.append("prompt_injection_detected")

    # Baseline next action: the most conservative state wins.
    if missing:
        next_action = "request_info"
    elif (injection or not vendor["risk_service_ok"] or "conflicting_vendor_evidence" in vendor["flags"]
          or "budget_unverified" in flags):
        next_action = "manual_review"
    else:
        next_action = "route_for_approval"

    return {
        "reference_date": ref_date.isoformat(),
        "department": department,
        "department_head": department_head,
        "annual_cost_usd": cost,
        "missing_information": missing,
        "approval_tier": tier,
        "budget": budget,
        "vendor": vendor,
        "existing_tool_matches": overlap,
        "review_triggers": triggers,
        "prompt_injection": injection,
        "required_approvals": order_approvals(approvals),
        "risk_flags": list(dict.fromkeys(flags)),
        "baseline_next_action": next_action,
        "human_review_required": True,
    }
