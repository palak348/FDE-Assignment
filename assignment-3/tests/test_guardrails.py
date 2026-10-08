"""Guardrails (src/guardrails.py): code stays authoritative over the LLM's decision."""
from __future__ import annotations

import pytest

from src.contracts import ProcurementDecision
from src.data_access import get_request
from src.guardrails import (LLMDecision, LLMEvidence, finalize, ground_evidence, normalize_action,
                            normalize_approval, normalize_flag)
from src.tools import RunContext


def context(request_id: str, *tools: str) -> RunContext:
    ctx = RunContext(get_request(request_id))
    for name in tools:
        ctx.call(name, {})
    return ctx


def decide(ctx: RunContext, **fields) -> ProcurementDecision:
    base = {"recommendation": "Route to the listed approvers.", "next_action": "route_for_approval",
            "next_step": "Send to approvers.", "required_approvals": [], "risk_flags": [], "evidence": []}
    base.update(fields)
    return finalize(ctx, LLMDecision.model_validate(base), architecture="single", model="test")


# REQ-1003: $18,000 CodeMate expansion, Engineering budget fine, source code access.
# Rules: Department Head, Procurement, Finance, Security; baseline route_for_approval.
REQ_1003_APPROVALS = ["Department Head", "Procurement", "Finance", "Security"]


# ---------------------------------------------------------------- (a) / (b) approvals

def test_missing_rule_required_approval_is_added():
    ctx = context("REQ-1003", "run_policy_checks")
    out = decide(ctx, required_approvals=["Department Head", "Procurement", "Security"])  # omits Finance
    assert out.required_approvals == REQ_1003_APPROVALS
    assert "added rule-required approval: Finance" in out.telemetry.guardrail_corrections


def test_cfo_added_by_llm_is_removed():
    ctx = context("REQ-1003", "run_policy_checks")
    out = decide(ctx, required_approvals=REQ_1003_APPROVALS + ["CFO"])
    assert "CFO" not in out.required_approvals
    assert any("CFO" in c for c in out.telemetry.guardrail_corrections)


def test_llm_may_add_review_role():
    ctx = context("REQ-1003", "run_policy_checks")
    out = decide(ctx, required_approvals=REQ_1003_APPROVALS + ["Privacy officer"])
    assert "Privacy" in out.required_approvals


# ---------------------------------------------------------------- (c) next action

def test_less_conservative_action_is_overridden_to_baseline():
    ctx = context("REQ-1007", "run_policy_checks")  # baseline manual_review (conflicting evidence)
    out = decide(ctx, next_action="route_for_approval")
    assert out.next_action == "manual_review"
    assert any(c.startswith("next_action") for c in out.telemetry.guardrail_corrections)


def test_missing_information_forces_request_info():
    ctx = context("REQ-1006", "run_policy_checks")
    out = decide(ctx, next_action="route_for_approval")
    assert out.next_action == "request_info"
    assert "missing_information" in out.risk_flags


def test_more_conservative_action_is_kept():
    ctx = context("REQ-1003", "run_policy_checks")
    out = decide(ctx, next_action="manual_review")
    assert out.next_action == "manual_review"


# ---------------------------------------------------------------- (d) evidence grounding

def test_evidence_from_uncalled_tool_is_dropped():
    ctx = context("REQ-1003", "run_policy_checks")
    out = decide(ctx, evidence=[{"source": "get_vendor_risk", "finding": "CodeMate review is approved"}])
    assert out.telemetry.ungrounded_evidence_dropped == 1
    assert all(e.source != "get_vendor_risk" for e in out.evidence)


def test_evidence_with_invented_number_is_dropped():
    ctx = context("REQ-1003", "get_request_details", "run_policy_checks")
    out = decide(ctx, evidence=[
        {"source": "get_request_details", "finding": "Annual cost is $18,000 for 30 users"},  # grounded
        {"source": "get_request_details", "finding": "Annual cost is $97,531"},              # invented
    ])
    assert out.telemetry.ungrounded_evidence_dropped == 1
    findings = [e.finding for e in out.evidence]
    assert "Annual cost is $18,000 for 30 users" in findings
    assert not any("97,531" in f for f in findings)


def test_ground_evidence_directly():
    ctx = context("REQ-1003", "get_request_details")
    kept, dropped = ground_evidence([
        LLMEvidence(source="get_request_details", finding="18000 USD requested"),
        LLMEvidence(source="check_budget", finding="within budget"),
        LLMEvidence(source="get_request_details", finding="   "),
    ], ctx)
    assert [e.finding for e in kept] == ["18000 USD requested"]
    assert dropped == 2


# ---------------------------------------------------------------- (e) rules-only fallback

@pytest.mark.parametrize("request_id, expected", [
    ("REQ-1003", "manual_review"),  # baseline route_for_approval is never kept without the LLM
    ("REQ-1006", "request_info"),
    ("REQ-1009", "manual_review"),
])
def test_llm_unavailable_fallback(request_id, expected):
    ctx = RunContext(get_request(request_id))
    out = finalize(ctx, None, architecture="single", model=None, llm_error="boom")
    assert out.next_action == expected
    assert "llm_unavailable" in out.risk_flags
    assert out.human_review_required is True
    assert "Procurement" in out.required_approvals
    assert set(ctx.rules()["required_approvals"]) <= set(out.required_approvals)
    assert out.telemetry.llm_error == "boom"
    assert out.evidence  # deterministic evidence is still provided


# ---------------------------------------------------------------- (f) autonomous approval wording

def test_autonomous_approval_wording_is_rewritten():
    ctx = context("REQ-1003", "run_policy_checks")
    out = decide(ctx, recommendation="The request is approved. Purchase now.",
                 required_approvals=REQ_1003_APPROVALS)
    assert "approved" not in out.recommendation.lower()
    assert "rewrote recommendation that implied autonomous approval" in out.telemetry.guardrail_corrections


# ---------------------------------------------------------------- (g) deterministic flags

def test_false_budget_insufficient_flag_is_removed():
    ctx = context("REQ-1003", "run_policy_checks")
    out = decide(ctx, risk_flags=["budget_insufficient", "security_review_required"])
    assert "budget_insufficient" not in out.risk_flags
    assert "removed model flag contradicted by rules: budget_insufficient" in out.telemetry.guardrail_corrections


def test_rule_flag_omitted_by_llm_is_added():
    ctx = context("REQ-1005", "run_policy_checks")
    out = decide(ctx, risk_flags=[])
    assert "budget_insufficient" in out.risk_flags


def test_judgment_flag_is_kept():
    ctx = context("REQ-1010", "run_policy_checks")
    out = decide(ctx, risk_flags=["existing tool overlap"])
    assert "existing_tool_overlap" in out.risk_flags


def test_guardrail_runs_policy_checks_if_model_did_not():
    ctx = RunContext(get_request("REQ-1003"))
    out = decide(ctx)
    assert "run_policy_checks" in out.telemetry.tool_names
    assert out.human_review_required is True


# ---------------------------------------------------------------- normalisers

@pytest.mark.parametrize("raw, expected", [
    ("Department Head", "Department Head"), ("Director of Marketing", "Department Head"),
    ("CFO", "CFO"), ("Finance team", "Finance"), ("InfoSec", "Security"), ("Legal counsel", "Legal"),
    ("Somebody", None),
])
def test_normalize_approval(raw, expected):
    assert normalize_approval(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("budget insufficient", "budget_insufficient"), ("Prompt-Injection detected", "prompt_injection_detected"),
    ("vendor_risk_unavailable", "vendor_risk_unavailable"), ("overlap with PixelCraft", "existing_tool_overlap"),
])
def test_normalize_flag(raw, expected):
    assert normalize_flag(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("route_for_approval", "route_for_approval"), ("Request more info", "request_info"),
    ("escalate", "manual_review"), ("gibberish", "manual_review"), ("", "manual_review"),
])
def test_normalize_action(raw, expected):
    assert normalize_action(raw) == expected


def test_llm_decision_coerces_loose_types():
    d = LLMDecision.model_validate({"required_approvals": "Finance", "risk_flags": None,
                                    "evidence": ["plain string", {"source": "x", "finding": "y"}]})
    assert d.required_approvals == ["Finance"]
    assert d.risk_flags == []
    assert d.evidence[0].finding == "plain string"


def test_grounding_accepts_thousands_separators_in_tool_output():
    """Regression: '$10,000' in a finding must match '$10,000' inside tool output text."""
    from src.data_access import get_request
    from src.guardrails import LLMEvidence, ground_evidence
    from src.tools import RunContext

    ctx = RunContext(get_request("REQ-1002"))
    ctx.call("run_policy_checks", {})  # its reasons contain "(>= $10,000)"
    kept, dropped = ground_evidence(
        [LLMEvidence(source="run_policy_checks", finding="Legal review required: new vendor with spend >= $10,000")], ctx)
    assert dropped == 0 and len(kept) == 1
