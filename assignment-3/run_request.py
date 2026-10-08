"""Run one procurement request through an agent architecture and print what happened.

    python run_request.py REQ-1006                       # Architecture A (single agent)
    python run_request.py REQ-1006 --architecture staged # Architecture B (analyst -> reviewer)
    python run_request.py REQ-1006 --architecture both   # A and B side by side
    python run_request.py REQ-1006 --json                # full decision + trace as JSON

Shows the tool calls the agent made, the evidence pack (B), what the guardrails changed,
and the final ProcurementDecision. No server is needed: the mock vendor API runs in-process.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import data_access as da  # noqa: E402
from src.solution import run_with_trace  # noqa: E402

LABELS = {"single": "A - Single agent", "staged": "B - Staged (Analyst -> Reviewer)"}


def _rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def show(request_id: str, architecture: str, llm=None) -> dict:
    decision, trace = run_with_trace(request_id, architecture, llm=llm)
    d = decision.model_dump()
    tel = d.get("telemetry") or {}

    print(f"\n=== {LABELS[architecture]}  |  {request_id}  |  model: {tel.get('model') or 'none'} ===")
    if trace.get("error"):
        print(f"LLM unavailable, rules-only decision returned: {trace['error']}")

    _rule("Tool calls")
    for c in trace["tool_calls"]:
        args = ", ".join(f"{k}={v!r}" for k, v in (c["arguments"] or {}).items())
        print(f"  {c['name']}({args})  [{c['latency_ms']:.0f} ms]")

    pack = trace.get("evidence_pack")
    if pack:
        _rule("Evidence pack (Analyst -> Reviewer hand-off)")
        print(f"  need: {pack.get('need_summary')}")
        print(f"  facts: {len(pack.get('facts') or [])}")
        for item in pack.get("unverified_or_missing") or []:
            print(f"  unverified/missing: {item}")
        for item in pack.get("untrusted_content_concerns") or []:
            print(f"  untrusted content: {item}")

    _rule("Guardrail corrections")
    corrections = tel.get("guardrail_corrections") or []
    print("\n".join(f"  {c}" for c in corrections) if corrections else "  none - model output already followed policy")

    _rule("Decision")
    print(f"  next action:        {d.get('next_action')}")
    print(f"  recommendation:     {d['recommendation']}")
    print(f"  required approvals: {', '.join(d['required_approvals']) or '-'}")
    print(f"  risk flags:         {', '.join(d['risk_flags']) or '-'}")
    print(f"  missing info:       {'; '.join(d['missing_information']) or '-'}")
    print(f"  next step:          {d['next_step']}")
    print(f"  human review:       {d['human_review_required']}")
    print(f"  evidence items:     {len(d['evidence'])}")

    _rule("Telemetry")
    print(f"  latency {trace['latency_ms'] / 1000:.1f} s | LLM calls {tel.get('llm_calls')} | "
          f"tool calls {tel.get('tool_calls')} | tokens "
          f"{(tel.get('prompt_tokens') or 0) + (tel.get('completion_tokens') or 0)}")
    return {"decision": d, "trace": trace}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("request_id", nargs="?", help="e.g. REQ-1001 (omit to list sample requests)")
    parser.add_argument("--architecture", choices=["single", "staged", "both"], default="single")
    parser.add_argument("--json", action="store_true", help="print the decision and trace as JSON")
    args = parser.parse_args()

    if not args.request_id:
        print("Sample requests:")
        for r in da.load_requests():
            print(f"  {r['request_id']}  {r.get('product_name')} ({r.get('vendor_name')})")
        return

    archs = ["single", "staged"] if args.architecture == "both" else [args.architecture]
    if args.json:
        out = {}
        for a in archs:
            decision, trace = run_with_trace(args.request_id, a)
            out[a] = {"decision": decision.model_dump(), "trace": trace}
        print(json.dumps(out, indent=2, default=str))
        return
    for a in archs:
        show(args.request_id, a)


if __name__ == "__main__":
    main()
