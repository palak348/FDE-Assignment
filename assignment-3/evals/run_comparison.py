"""Architecture comparison: run the SAME case set on every architecture and score it.

    python evals/run_comparison.py                      # single + staged, 1 run
    python evals/run_comparison.py --runs 2 --delay 5   # repeat to see variance
    python evals/run_comparison.py --architectures rules  # deterministic-only baseline (no LLM)

Scores both the final decision (after deterministic guardrails) and the raw model
output (before guardrails), so the contribution of code vs. model is visible.
Writes evals/results/{comparison_runs.csv, evaluation_results.csv, summary.md, raw/*.json}.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("VENDOR_RISK_MODE", "auto")

from src.data_access import get_request  # noqa: E402
from src.guardrails import finalize, normalize_action, normalize_approval, normalize_flag  # noqa: E402
from src.solution import run_with_trace  # noqa: E402
from src.tools import RunContext  # noqa: E402

RESULTS = ROOT / "evals" / "results"


# ---------------------------------------------------------------- scoring

def score(approvals: list[str], flags: list[str], missing: list[str], action: str, recommendation: str,
          human_review: bool, exp: dict) -> dict:
    appr = {a for a in approvals if a}
    flg = set(flags)
    miss_text = [m.lower() for m in missing]
    checks: dict[str, bool] = {}
    problems: list[str] = []

    missing_appr = [a for a in exp.get("approvals_all", []) if a not in appr]
    extra_appr = [a for a in exp.get("approvals_none", []) if a in appr]
    checks["approvals"] = not missing_appr and not extra_appr
    if missing_appr:
        problems.append(f"missing approvals {missing_appr}")
    if extra_appr:
        problems.append(f"unexpected approvals {extra_appr}")

    missing_flags = [f for f in exp.get("flags_all", []) if f not in flg]
    bad_flags = [f for f in exp.get("flags_none", []) if f in flg]
    checks["flags"] = not missing_flags and not bad_flags
    if missing_flags:
        problems.append(f"missing flags {missing_flags}")
    if bad_flags:
        problems.append(f"unexpected flags {bad_flags}")

    ok_missing = True
    for group in exp.get("missing_any_groups", []):
        if not any(tok in m for m in miss_text for tok in group):
            ok_missing = False
            problems.append(f"missing-info lacks one of {group}")
    max_missing = exp.get("max_missing_information")
    if max_missing is not None and len(missing) > max_missing:
        ok_missing = False
        problems.append(f"{len(missing)} missing-info items > {max_missing}")
    checks["missing_info"] = ok_missing

    checks["next_action"] = action in exp.get("acceptable_next_actions", [action])
    if not checks["next_action"]:
        problems.append(f"next_action {action} not in {exp.get('acceptable_next_actions')}")

    pattern = exp.get("recommendation_must_not_match")
    checks["no_unsafe_text"] = not (pattern and re.search(pattern, recommendation or ""))
    if not checks["no_unsafe_text"]:
        problems.append("recommendation repeats injected/unsafe claim")

    checks["human_review"] = bool(human_review)
    checks["policy_followed"] = checks["approvals"] and checks["flags"] and checks["missing_info"]
    checks["escalation_correct"] = checks["human_review"] and checks["next_action"] and checks["approvals"]
    checks["pass"] = all(checks[k] for k in ("approvals", "flags", "missing_info", "next_action",
                                             "no_unsafe_text", "human_review"))
    return {"checks": checks, "problems": problems}


def score_final(decision, exp: dict) -> dict:
    return score(decision.required_approvals, decision.risk_flags, decision.missing_information,
                 decision.next_action or "", decision.recommendation, decision.human_review_required, exp)


def score_raw(raw: dict | None, exp: dict) -> dict | None:
    """Score the model's own output before deterministic guardrails."""
    if not raw:
        return None
    approvals = [normalize_approval(str(a)) for a in raw.get("required_approvals") or []]
    flags = [normalize_flag(str(f)) for f in raw.get("risk_flags") or []]
    missing = [str(m) for m in raw.get("missing_information") or []]
    return score([a for a in approvals if a], flags, missing, normalize_action(str(raw.get("next_action", ""))),
                 str(raw.get("recommendation", "")), True, exp)


def run_rules_only(request_id: str):
    """Deterministic-only comparator: no LLM, guardrail fallback path."""
    start = time.perf_counter()
    ctx = RunContext(get_request(request_id))
    decision = finalize(ctx, None, architecture="rules", model=None, llm_error="rules-only baseline (no LLM)")
    return decision, {"latency_ms": round((time.perf_counter() - start) * 1000, 1), "error": None}


# ---------------------------------------------------------------- main

def pct(values: list[bool]) -> str:
    return f"{100 * sum(values) / len(values):.0f}%" if values else "-"


def avg(values: list[float]) -> float:
    return round(statistics.mean(values), 1) if values else 0.0


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))], 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--architectures", nargs="+", default=["single", "staged"],
                        choices=["single", "staged", "rules"])
    parser.add_argument("--cases", default=str(ROOT / "evals" / "extended_cases.json"))
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--delay", type=float, default=0.0, help="seconds to sleep between cases (rate limits)")
    parser.add_argument("--only", nargs="*", help="subset of case_ids")
    parser.add_argument("--tag", default="", help="suffix for output files")
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c["case_id"] in set(args.only)]
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "raw").mkdir(exist_ok=True)
    tag = f"_{args.tag}" if args.tag else ""

    rows: list[dict] = []
    for run in range(1, args.runs + 1):
        for case in cases:
            pending = list(args.architectures)
            while pending:
                arch = pending.pop(0)
                print(f"[run {run}] {case['case_id']:7s} {arch:7s} ...", end=" ", flush=True)
                if arch == "rules":
                    decision, trace = run_rules_only(case["request_id"])
                else:
                    decision, trace = run_with_trace(case["request_id"], arch)
                    # Pairing: if the model changed (daily-quota fallback) since the case's
                    # earlier architectures ran, re-run those so A and B share one model.
                    model = decision.telemetry.model if decision.telemetry else None
                    stale = [r for r in rows if r["run"] == run and r["case_id"] == case["case_id"]
                             and r["architecture"] != "rules" and r["model"] != model]
                    if stale and model:
                        print(f"(model now {model}; re-running {[r['architecture'] for r in stale]} for pairing)", end=" ")
                        for r in stale:
                            rows.remove(r)
                            pending.append(r["architecture"])
                final = score_final(decision, case["expectations"])
                raw = score_raw(trace.get("raw_llm_decision"), case["expectations"])
                tel = decision.telemetry
                llm_evidence = len((trace.get("raw_llm_decision") or {}).get("evidence") or [])
                dropped = tel.ungrounded_evidence_dropped or 0 if tel else 0
                row = {
                    "run": run, "case_id": case["case_id"], "request_id": case["request_id"],
                    "edge_case": case.get("edge_case", ""), "architecture": arch,
                    "model": (tel.model if tel else "") or "",
                    "pass": final["checks"]["pass"],
                    "raw_llm_pass": raw["checks"]["pass"] if raw else "",
                    "correct_next_action": final["checks"]["next_action"],
                    "raw_llm_next_action_ok": raw["checks"]["next_action"] if raw else "",
                    "policy_followed": final["checks"]["policy_followed"],
                    "raw_llm_policy_followed": raw["checks"]["policy_followed"] if raw else "",
                    "human_escalation_correct": final["checks"]["escalation_correct"],
                    "grounded_evidence": dropped == 0 and len(decision.evidence) > 0,
                    "llm_evidence_items": llm_evidence, "ungrounded_dropped": dropped,
                    "next_action": decision.next_action,
                    "raw_llm_next_action": normalize_action(str((trace.get("raw_llm_decision") or {}).get("next_action", "")))
                    if trace.get("raw_llm_decision") else "",
                    "latency_ms": trace["latency_ms"],
                    "cached": bool(tel and tel.cached_llm_calls),
                    "llm_calls": tel.llm_calls if tel else 0, "tool_calls": tel.tool_calls if tel else 0,
                    "prompt_tokens": tel.prompt_tokens if tel else 0,
                    "completion_tokens": tel.completion_tokens if tel else 0,
                    "guardrail_corrections": len(tel.guardrail_corrections) if tel else 0,
                    "error": trace.get("error") or "",
                    "problems": " | ".join(final["problems"]),
                    "raw_llm_problems": " | ".join(raw["problems"]) if raw else "",
                }
                rows.append(row)
                (RESULTS / "raw" / f"{arch}_{case['case_id']}_run{run}{tag}.json").write_text(json.dumps({
                    "case": case, "decision": decision.model_dump(),
                    "raw_llm_decision": trace.get("raw_llm_decision"), "evidence_pack": trace.get("evidence_pack"),
                    "tool_calls": [{k: v for k, v in c.items() if k != "result"} for c in trace.get("tool_calls", [])],
                    "error": trace.get("error"), "latency_ms": trace["latency_ms"]}, indent=2, default=str),
                    encoding="utf-8")
                status = "PASS" if row["pass"] else "FAIL"
                print(f"{status} {row['next_action']:18s} {row['latency_ms']:>8.0f} ms  llm={row['llm_calls']} "
                      f"tools={row['tool_calls']} {('ERR ' + row['error'][:60]) if row['error'] else ''}")
                for p in final["problems"]:
                    print(f"        - {p}")
                if args.delay and arch != "rules":
                    time.sleep(args.delay)

    write_outputs(rows, args, tag)


def write_outputs(rows: list[dict], args, tag: str) -> None:
    with (RESULTS / f"comparison_runs{tag}.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    # Per-run results in the standard evaluation CSV format
    with (RESULTS / f"evaluation_results{tag}.csv").open("w", newline="", encoding="utf-8") as f:
        cols = ["case_id", "architecture", "correct_next_action", "grounded_evidence", "policy_followed",
                "human_escalation_correct", "latency_ms", "llm_calls", "tool_calls", "notes"]
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for r in rows:
            writer.writerow({**{c: r[c] for c in cols if c in r},
                             "notes": f"run {r['run']}; {r['problems'] or 'all checks passed'}"
                                      + (f"; error: {r['error']}" if r["error"] else "")})

    lines = [f"# Architecture comparison results{(' (' + args.tag + ')') if args.tag else ''}", "",
             f"Cases: {len({r['case_id'] for r in rows})} | runs: {args.runs} | architectures: "
             f"{', '.join(args.architectures)} | models used: "
             f"{', '.join(sorted({r['model'] for r in rows if r['model']})) or 'none (rules only)'}", "",
             "| Metric | " + " | ".join(args.architectures) + " |",
             "|---|" + "---:|" * len(args.architectures)]

    def metric(name: str, fn) -> None:
        vals = []
        for arch in args.architectures:
            sub = [r for r in rows if r["architecture"] == arch]
            vals.append(str(fn(sub)) if sub else "-")
        lines.append(f"| {name} | " + " | ".join(vals) + " |")

    llm_rows = lambda sub: [r for r in sub if r["raw_llm_pass"] != ""]  # noqa: E731
    metric("Cases passing all checks (final)", lambda s: f"{sum(r['pass'] for r in s)}/{len(s)} ({pct([r['pass'] for r in s])})")
    metric("Raw model output passing (pre-guardrail)", lambda s: pct([r['raw_llm_pass'] for r in llm_rows(s)]))
    metric("Correct next action (final)", lambda s: pct([r['correct_next_action'] for r in s]))
    metric("Correct next action (raw model)", lambda s: pct([r['raw_llm_next_action_ok'] for r in llm_rows(s)]))
    metric("Policy followed (final)", lambda s: pct([r['policy_followed'] for r in s]))
    metric("Policy followed (raw model)", lambda s: pct([r['raw_llm_policy_followed'] for r in llm_rows(s)]))
    metric("Human escalation correct", lambda s: pct([r['human_escalation_correct'] for r in s]))
    metric("Evidence fully grounded", lambda s: pct([r['grounded_evidence'] for r in s]))
    metric("Ungrounded model evidence dropped (total)", lambda s: sum(r['ungrounded_dropped'] for r in s))
    live = lambda sub: [r for r in sub if not r.get("cached")]  # noqa: E731  cached replies have no real latency
    metric("Avg latency (ms, uncached runs)", lambda s: avg([r['latency_ms'] for r in live(s)]))
    metric("p95 latency (ms, uncached runs)", lambda s: p95([r['latency_ms'] for r in live(s)]))
    metric("Avg LLM calls", lambda s: avg([r['llm_calls'] for r in s]))
    metric("Avg tool calls", lambda s: avg([r['tool_calls'] for r in s]))
    metric("Avg tokens (prompt+completion)", lambda s: avg([r['prompt_tokens'] + r['completion_tokens'] for r in s]))
    metric("Avg guardrail corrections", lambda s: avg([r['guardrail_corrections'] for r in s]))
    metric("Runs with LLM errors", lambda s: sum(1 for r in s if r['error']))

    lines += ["", "## Per-case results (final decision; pass count over runs)", "",
              "| Case | Edge case | " + " | ".join(args.architectures) + " |",
              "|---|---|" + "---|" * len(args.architectures)]
    for case_id in dict.fromkeys(r["case_id"] for r in rows):
        sub = [r for r in rows if r["case_id"] == case_id]
        cells = []
        for arch in args.architectures:
            a = [r for r in sub if r["architecture"] == arch]
            actions = ", ".join(sorted({r["next_action"] for r in a}))
            cells.append(f"{sum(r['pass'] for r in a)}/{len(a)} ({actions})")
        lines.append(f"| {case_id} | {sub[0]['edge_case']} | " + " | ".join(cells) + " |")

    failures = [r for r in rows if not r["pass"] or (r["raw_llm_pass"] is False)]
    if failures:
        lines += ["", "## Failures and guardrail catches", ""]
        for r in failures:
            if not r["pass"]:
                lines.append(f"- **{r['architecture']} {r['case_id']} run {r['run']} (final FAIL)**: {r['problems']}"
                             + (f" — error: {r['error']}" if r["error"] else ""))
            else:
                lines.append(f"- {r['architecture']} {r['case_id']} run {r['run']}: raw model failed "
                             f"({r['raw_llm_problems']}) — corrected by deterministic guardrails")
    out = RESULTS / f"summary{tag}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines[:22]))
    print(f"\nWrote {out.relative_to(ROOT)} and CSVs in {RESULTS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
