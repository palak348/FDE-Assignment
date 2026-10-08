"""The terminal runner prints each agent step for both architectures (offline)."""
from __future__ import annotations

import run_request
from src.llm import ScriptedLLM
from tests.test_agents_offline import (DECISION_1003, EVIDENCE_PACK, FIRST_TURN_CALLS, json_response,
                                       tool_calls_response)


def test_single_agent_steps_are_printed(capsys):
    llm = ScriptedLLM([tool_calls_response(*FIRST_TURN_CALLS), json_response(DECISION_1003)])
    out = run_request.show("REQ-1003", "single", llm=llm)
    printed = capsys.readouterr().out
    assert "A - Single agent" in printed
    assert "get_vendor_risk(vendor_name='CodeMate')" in printed
    assert "human review:       True" in printed
    assert out["decision"]["request_id"] == "REQ-1003"


def test_staged_prints_evidence_pack(capsys):
    llm = ScriptedLLM([tool_calls_response(*FIRST_TURN_CALLS[:2]), json_response(EVIDENCE_PACK),
                       json_response(DECISION_1003)])
    run_request.show("REQ-1003", "staged", llm=llm)
    printed = capsys.readouterr().out
    assert "Evidence pack (Analyst -> Reviewer hand-off)" in printed
    assert "Expand the coding assistant" in printed


def test_llm_failure_prints_rules_only_decision(capsys):
    run_request.show("REQ-1003", "single", llm=ScriptedLLM([]))
    printed = capsys.readouterr().out
    assert "rules-only decision returned" in printed
    assert "llm_unavailable" in printed
