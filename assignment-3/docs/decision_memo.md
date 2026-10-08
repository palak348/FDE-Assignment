# Architecture Decision Memo

## Decision
Ship **Architecture A — the single Procurement Agent** with deterministic tools and guardrails.

## Evidence
17 cases (6 public + 11 edge cases) × 2 uncached runs, same model (`gemini-3.1-flash-lite`), tools and guardrails (`evals/results/summary_combined.md`).

| Metric | Single agent | Staged / 2-agent |
|---|---:|---:|
| Cases passing all checks (final) | 32/34 (94%) | 32/34 (94%) |
| Raw model output following policy (pre-guardrail) | 100% | 82% |
| Correct next action | 94% | 94% |
| Evidence fully grounded | 100% | 100% |
| Avg / p95 latency | 11.8 s / 22.3 s | 20.8 s / 34.1 s |
| Avg LLM calls / tool calls | 2 / 6 | 3 / 6 |
| Avg tokens | 7.1k | 10.9k |
| Notable failures | EXT-06: suggested reusing the existing SignFlow licence for a $1,000 add-on | EXT-04: escalated a $950 training pack instead of routing it |

Both pass the public harness 6/6. A rules-only baseline (no LLM) passes 12/17: without model judgment, clean requests fall back to manual review.

## Trade-offs
- **Quality is equal.** Each architecture fails one case, consistently across runs, and both failures are over-cautious (more human review), never unsafe. No run approved anything, accepted the injected "CFO-approved" claim, or assumed a favourable vendor status during the API outage.
- **B costs more for no accuracy gain:** one extra LLM call, +77% latency, +52% tokens.
- **B's reviewer is less policy-aligned on its own:** its raw output invented missing-information items (PUB-04, EXT-01, EXT-04), failing policy checks in 18% of runs and needing 8× more guardrail corrections (0.8 vs 0.1 per run). The deterministic rules engine already plays the "independent reviewer" role.
- **Grounding is equal:** every model evidence item in both architectures cited a called tool and only numbers present in its output (100%).

## Risks / limitations
- One small free model, 17 cases, 2 runs; expectations hand-written from the policy before the runs. Validate on a larger reviewer-labelled set before production.
- "Reuse existing vs. new purchase" is the least stable judgment; Procurement should review it and the policy may need clearer guidance.
- The free tier allows about 20 requests/day on some models; production needs a paid key or gateway.

## Why this is the right MVP
The decisions that must be right — thresholds, budget, Security/Privacy/Legal triggers, expiry, conflicts, outages, injection — are made by code and are identical in both architectures. The LLM's job is narrow: interpret the request, judge overlap and credibility, and explain the recommendation from tool evidence. One agent does that as well as two, at 43% lower latency and a third fewer tokens, with fewer moving parts. A second agent is only worth adding if future evaluations show the single agent missing evidence a dedicated reviewer would catch; the harness is in place to measure that.
