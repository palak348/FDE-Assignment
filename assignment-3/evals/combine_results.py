"""Combine several comparison runs (e.g. tags run1 run2) into one summary.

    python evals/combine_results.py run1 run2
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.run_comparison import RESULTS, write_outputs  # noqa: E402

BOOL_COLS = {"pass", "raw_llm_pass", "correct_next_action", "raw_llm_next_action_ok", "policy_followed",
             "raw_llm_policy_followed", "human_escalation_correct", "grounded_evidence", "cached"}
INT_COLS = {"llm_evidence_items", "ungrounded_dropped", "llm_calls", "tool_calls", "prompt_tokens",
            "completion_tokens", "guardrail_corrections"}


def load(tag: str, run_no: int) -> list[dict]:
    rows = []
    with (RESULTS / f"comparison_runs_{tag}.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for k in BOOL_COLS:
                if k in r:
                    r[k] = {"True": True, "False": False}.get(r[k], "")
            for k in INT_COLS:
                r[k] = int(r[k] or 0)
            r["latency_ms"] = float(r["latency_ms"])
            raw = RESULTS / "raw" / f"{r['architecture']}_{r['case_id']}_run{r['run']}_{tag}.json"
            if "cached" not in r and raw.is_file():  # older runs: detect replayed (cached) model calls
                tel = json.loads(raw.read_text(encoding="utf-8"))["decision"]["telemetry"] or {}
                r["cached"] = bool(tel.get("llm_calls")) and not tel.get("llm_latency_ms")
            r["run"] = run_no
            rows.append(r)
    return rows


def main() -> None:
    tags = sys.argv[1:] or ["run1", "run2"]
    rows = [r for i, tag in enumerate(tags, 1) for r in load(tag, i)]
    archs = list(dict.fromkeys(r["architecture"] for r in rows))
    write_outputs(rows, SimpleNamespace(architectures=archs, runs=len(tags), tag="combined"), "_combined")


if __name__ == "__main__":
    main()
