# Evaluation

| File | Purpose |
|---|---|
| `public_cases.json` | The 6 public evaluation cases |
| `extended_cases.json` | All 17 cases with expected approvals, flags, missing information and next actions |
| `extra_requests.json` | Synthetic requests for the edge cases not covered by `data/requests.json` |
| `run_public_evals.py` | Provided public harness: schema and minimum-expectation checks |
| `run_comparison.py` | Runs both architectures on the same cases and scores them |
| `combine_results.py` | Merges several runs into one summary |
| `regrade_grounding.py` | Re-scores evidence grounding on saved runs |
| `results/` | Saved CSVs, summaries and raw traces (decision, model output, tool calls) |

```bash
python evals/run_public_evals.py --architecture single
python evals/run_public_evals.py --architecture staged
python evals/run_comparison.py --runs 1 --tag run1
python evals/run_comparison.py --runs 1 --tag run2
python evals/combine_results.py run1 run2
python evals/run_comparison.py --architectures rules   # no-LLM baseline
```

The runners don't need `run_local.py`: if no server is listening, the vendor client uses the mock API in-process.
