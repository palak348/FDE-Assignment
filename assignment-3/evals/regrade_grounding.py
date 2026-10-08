"""Re-grade evidence grounding for saved runs without calling the LLM.

Tools are deterministic, so the recorded tool calls (name + arguments in
evals/results/raw/*.json) are replayed in-process and the saved raw model
evidence is re-checked with the current `ground_evidence`. Used after fixing a
grounding-check bug (thousands separators) found during evaluation.

    python evals/regrade_grounding.py run1 run2
"""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("VENDOR_RISK_MODE", "inprocess")

from src.data_access import get_request  # noqa: E402
from src.guardrails import LLMDecision, ground_evidence  # noqa: E402
from src.tools import RunContext  # noqa: E402

RESULTS = ROOT / "evals" / "results"


def regrade(tag: str) -> None:
    path = RESULTS / f"comparison_runs_{tag}.csv"
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    changed = 0
    for r in rows:
        raw_file = RESULTS / "raw" / f"{r['architecture']}_{r['case_id']}_run{r['run']}_{tag}.json"
        saved = json.loads(raw_file.read_text(encoding="utf-8"))
        if not saved.get("raw_llm_decision"):
            continue
        ctx = RunContext(get_request(r["request_id"]))
        for call in saved["tool_calls"]:
            ctx.call(call["name"], call["arguments"])
        _, dropped = ground_evidence(LLMDecision.model_validate(saved["raw_llm_decision"]).evidence, ctx)
        if str(dropped) != r["ungrounded_dropped"]:
            changed += 1
            if dropped == 0 and int(r["ungrounded_dropped"]) > 0:
                # the "dropped N ungrounded evidence item(s)" guardrail correction no longer applies
                r["guardrail_corrections"] = int(r["guardrail_corrections"]) - 1
            print(f"{tag} {r['architecture']} {r['case_id']}: ungrounded {r['ungrounded_dropped']} -> {dropped}")
        r["ungrounded_dropped"] = dropped
        r["grounded_evidence"] = dropped == 0
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"{tag}: {changed} row(s) re-graded")


if __name__ == "__main__":
    for t in sys.argv[1:] or ["run1", "run2"]:
        regrade(t)
