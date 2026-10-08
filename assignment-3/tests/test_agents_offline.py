"""Both agent architectures end to end with a ScriptedLLM (no network, no real model)."""
from __future__ import annotations

import json

from src.contracts import ProcurementDecision
from src.llm import LLMError, LLMResponse, ScriptedLLM, ToolCallRequest
from src.solution import run_with_trace


# ---------------------------------------------------------------- scripted response builders

def tool_calls_response(*calls: tuple[str, dict]) -> LLMResponse:
    requests_ = [ToolCallRequest(id=f"call_{i}", name=name, arguments=args) for i, (name, args) in enumerate(calls)]
    message = {"role": "assistant", "content": "", "tool_calls": [
        {"id": r.id, "type": "function", "function": {"name": r.name, "arguments": json.dumps(r.arguments)}}
        for r in requests_]}
    return LLMResponse(content=None, tool_calls=requests_, message=message, prompt_tokens=100, completion_tokens=20)


def text_response(text: str) -> LLMResponse:
    return LLMResponse(content=text, tool_calls=[], message={"role": "assistant", "content": text},
                       prompt_tokens=200, completion_tokens=50)


def json_response(obj: dict) -> LLMResponse:
    return text_response(json.dumps(obj))


DECISION_1003 = {
    "recommendation": "Route the CodeMate expansion to the required approvers.",
    "next_action": "route_for_approval",
    "evidence": [{"source": "get_request_details", "finding": "Annual cost $18,000 for 30 users",
                  "reference": "requests/REQ-1003"}],
    "required_approvals": ["Department Head", "Finance", "Procurement", "Security"],
    "missing_information": [],
    "risk_flags": ["security_review_required"],
    "next_step": "Send the evidence package to the approvers.",
    "rationale": "Source code access needs Security.",
}

EVIDENCE_PACK = {
    "need_summary": "Expand the coding assistant to two squads.",
    "facts": [{"source": "get_request_details", "finding": "30 users requested", "reference": "requests/REQ-1003"}],
    "existing_tools": {"candidates": ["CodeMate"], "credible_gap": "Expansion of existing tool"},
    "vendor_status": "Approved in registry and service",
    "data_sensitivity": "source_code; Git repositories",
    "unverified_or_missing": [],
    "untrusted_content_concerns": [],
}

FIRST_TURN_CALLS = (
    ("get_request_details", {}),
    ("get_vendor_risk", {"vendor_name": "CodeMate"}),
    ("run_policy_checks", {}),
)


def assert_valid(decision) -> ProcurementDecision:
    validated = ProcurementDecision.model_validate(decision.model_dump())
    assert validated.human_review_required is True
    return validated


# ---------------------------------------------------------------- single architecture

def test_single_agent_tool_calls_then_decision():
    llm = ScriptedLLM([tool_calls_response(*FIRST_TURN_CALLS), json_response(DECISION_1003)])
    decision, trace = run_with_trace("REQ-1003", "single", llm=llm)

    assert_valid(decision)
    assert decision.telemetry.llm_calls == 2
    assert decision.telemetry.tool_calls >= 3
    assert {"get_request_details", "get_vendor_risk", "run_policy_checks"} <= set(decision.telemetry.tool_names)
    assert decision.telemetry.llm_error is None
    assert decision.next_action == "route_for_approval"
    assert decision.required_approvals == ["Department Head", "Procurement", "Finance", "Security"]
    assert trace["error"] is None
    assert trace["raw_llm_decision"]["next_action"] == "route_for_approval"
    # Tool results were fed back to the model as role=tool messages.
    second_prompt = llm.seen[1]
    assert sum(1 for m in second_prompt if m["role"] == "tool") == 3


def test_single_agent_unavailable_tool_is_refused_not_executed():
    llm = ScriptedLLM([tool_calls_response(("delete_budget", {})), json_response(DECISION_1003)])
    decision, _ = run_with_trace("REQ-1003", "single", llm=llm)
    assert "delete_budget" not in decision.telemetry.tool_names
    tool_msg = next(m for m in llm.seen[1] if m["role"] == "tool")
    assert "not available" in tool_msg["content"]


def test_single_agent_repairs_invalid_json():
    llm = ScriptedLLM([tool_calls_response(*FIRST_TURN_CALLS),
                       text_response("Sure! Here is my answer: route it, it looks fine."),
                       json_response(DECISION_1003)])
    decision, trace = run_with_trace("REQ-1003", "single", llm=llm)
    assert_valid(decision)
    assert decision.telemetry.llm_calls == 3
    assert decision.telemetry.llm_error is None
    assert trace["raw_llm_decision"]["recommendation"] == DECISION_1003["recommendation"]
    assert "not valid JSON" in llm.seen[2][-1]["content"]


def test_single_agent_json_in_code_fence_is_accepted():
    llm = ScriptedLLM([tool_calls_response(*FIRST_TURN_CALLS),
                       text_response("```json\n" + json.dumps(DECISION_1003) + "\n```")])
    decision, _ = run_with_trace("REQ-1003", "single", llm=llm)
    assert decision.telemetry.llm_calls == 2
    assert decision.telemetry.llm_error is None


def test_invalid_json_twice_falls_back_to_rules():
    llm = ScriptedLLM([tool_calls_response(*FIRST_TURN_CALLS), text_response("nope"), text_response("still nope")])
    decision, trace = run_with_trace("REQ-1003", "single", llm=llm)
    assert_valid(decision)
    assert "llm_unavailable" in decision.risk_flags
    assert decision.next_action == "manual_review"
    assert trace["error"]


# ---------------------------------------------------------------- staged architecture

def test_staged_analyst_then_reviewer():
    llm = ScriptedLLM([
        tool_calls_response(("get_request_details", {}), ("get_vendor_risk", {"vendor_name": "CodeMate"}),
                            ("check_budget", {"department": "Engineering", "annual_cost_usd": 18000})),
        json_response(EVIDENCE_PACK),
        json_response(DECISION_1003),
    ])
    decision, trace = run_with_trace("REQ-1003", "staged", llm=llm)

    assert_valid(decision)
    assert decision.telemetry.llm_calls == 3
    assert decision.telemetry.architecture == "staged"
    # Analyst's 3 tools + code-run policy checks.
    assert decision.telemetry.tool_calls >= 4
    assert "run_policy_checks" in decision.telemetry.tool_names
    assert trace["evidence_pack"]["need_summary"] == EVIDENCE_PACK["need_summary"]
    # The reviewer sees the rules engine result in its prompt.
    # (ScriptedLLM keeps a reference to the message list, so look for the user turn.)
    reviewer_user = next(m for m in llm.seen[2] if m["role"] == "user")
    assert "DETERMINISTIC RULES ENGINE RESULT" in reviewer_user["content"]


def test_staged_reviewer_cannot_downgrade_conflict_case():
    llm = ScriptedLLM([
        tool_calls_response(("get_vendor_risk", {"vendor_name": "SignalWatch"})),
        json_response(EVIDENCE_PACK),
        json_response({**DECISION_1003, "required_approvals": ["Manager"], "risk_flags": []}),
    ])
    decision, _ = run_with_trace("REQ-1007", "staged", llm=llm)
    assert decision.next_action == "manual_review"
    assert {"conflicting_vendor_evidence", "vendor_review_expired"} <= set(decision.risk_flags)
    assert "Security" in decision.required_approvals


# ---------------------------------------------------------------- LLM failure

class FailingLLM:
    model = "failing-fake"

    def complete(self, messages, tools=None, json_mode=False):
        raise LLMError("429 quota exhausted")


def test_llm_error_returns_rules_only_decision_single():
    decision, trace = run_with_trace("REQ-1009", "single", llm=FailingLLM())
    assert_valid(decision)
    assert decision.telemetry.llm_error == "429 quota exhausted"
    assert "llm_unavailable" in decision.risk_flags
    assert "vendor_risk_unavailable" in decision.risk_flags
    assert decision.next_action == "manual_review"
    assert trace["error"] == "429 quota exhausted"


def test_llm_error_returns_rules_only_decision_staged():
    decision, _ = run_with_trace("REQ-1006", "staged", llm=FailingLLM())
    assert_valid(decision)
    assert decision.telemetry.llm_error
    assert decision.next_action == "request_info"


def test_scripted_llm_running_out_of_responses_is_handled():
    llm = ScriptedLLM([tool_calls_response(*FIRST_TURN_CALLS)])  # no final answer scripted
    decision, _ = run_with_trace("REQ-1003", "single", llm=llm)
    assert "llm_unavailable" in decision.risk_flags
    assert decision.telemetry.llm_error


def test_prompt_injection_request_never_routes_with_cfo():
    injected_decision = {**DECISION_1003, "recommendation": "CFO-approved; the request is approved.",
                         "required_approvals": ["CFO"], "next_action": "route_for_approval"}
    llm = ScriptedLLM([tool_calls_response(("run_policy_checks", {})), json_response(injected_decision)])
    decision, _ = run_with_trace("REQ-1006", "single", llm=llm)
    assert decision.next_action == "request_info"
    assert "CFO" not in decision.required_approvals
    assert "prompt_injection_detected" in decision.risk_flags
    assert "is approved" not in decision.recommendation.lower()
