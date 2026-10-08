"""Architecture A - single Procurement Agent with all tools."""
from __future__ import annotations

from src.agents.common import SAFETY_RULES, parse_or_repair, policy_block, request_block, tool_loop
from src.guardrails import LLM_DECISION_JSON_SPEC, LLMDecision
from src.llm import ChatModel
from src.tools import RunContext

TOOLS = ["get_request_details", "check_budget", "search_software_catalog", "get_vendor_registry",
         "get_vendor_risk", "run_policy_checks"]

SYSTEM_PROMPT = f"""You are the Procurement Agent of an internal procurement copilot.
Your job: understand the purchase request, gather evidence with tools, and recommend the next human action.

{SAFETY_RULES}

HOW TO WORK
- In your FIRST turn call these tools together, in parallel: get_request_details, check_budget (requester department + annual cost if known), search_software_catalog (category, vendor_name, product_name), get_vendor_registry, get_vendor_risk, run_policy_checks.
- Call more tools only if something is genuinely unclear.
- Then answer with the decision JSON. Every evidence item must cite the exact tool name it came from.
- next_action meanings: route_for_approval (complete request, send to listed approvers), request_info (material information missing), reuse_existing (an existing approved tool likely covers the need without a credible gap), manual_review (evidence unavailable, conflicting, or untrusted content), reject (clearly not allowed).

{LLM_DECISION_JSON_SPEC}
"""


def run(ctx: RunContext, llm: ChatModel) -> tuple[LLMDecision, dict]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + policy_block()},
        {"role": "user", "content": request_block(ctx)},
    ]
    loop = tool_loop(llm, ctx, messages, TOOLS)
    raw = parse_or_repair(llm, ctx, messages, loop.final_text, LLM_DECISION_JSON_SPEC)
    decision = LLMDecision.model_validate(raw)
    return decision, {"raw_llm_decision": raw, "tool_rounds": loop.rounds}
