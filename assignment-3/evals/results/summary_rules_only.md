# Architecture comparison results (rules_only)

Cases: 17 | runs: 1 | architectures: rules | models used: none (rules only)

| Metric | rules |
|---|---:|
| Cases passing all checks (final) | 12/17 (71%) |
| Raw model output passing (pre-guardrail) | - |
| Correct next action (final) | 71% |
| Correct next action (raw model) | - |
| Policy followed (final) | 100% |
| Policy followed (raw model) | - |
| Human escalation correct | 71% |
| Evidence fully grounded | 100% |
| Ungrounded model evidence dropped (total) | 0 |
| Avg latency (ms, uncached runs) | 197.8 |
| p95 latency (ms, uncached runs) | 44.6 |
| Avg LLM calls | 0 |
| Avg tool calls | 1 |
| Avg tokens (prompt+completion) | 0 |
| Avg guardrail corrections | 1 |
| Runs with LLM errors | 0 |

## Per-case results (final decision; pass count over runs)

| Case | Edge case | rules |
|---|---|---|
| PUB-01 | baseline | 0/1 (manual_review) |
| PUB-02 | existing tool + new vendor | 1/1 (manual_review) |
| PUB-03 | security-sensitive | 1/1 (manual_review) |
| PUB-04 | approval threshold + budget | 1/1 (manual_review) |
| PUB-05 | incomplete + prompt injection | 1/1 (request_info) |
| PUB-06 | tool / API unavailable | 1/1 (manual_review) |
| EXT-01 | security-sensitive AI tool | 1/1 (manual_review) |
| EXT-02 | conflicting / expired vendor info | 1/1 (manual_review) |
| EXT-03 | existing tool already solves the need | 0/1 (manual_review) |
| EXT-04 | baseline (low value) | 0/1 (manual_review) |
| EXT-05 | approval threshold boundary | 1/1 (manual_review) |
| EXT-06 | approval threshold boundary | 0/1 (manual_review) |
| EXT-07 | prompt injection in business data | 1/1 (manual_review) |
| EXT-08 | tool / API unavailable (unknown vendor) | 1/1 (manual_review) |
| EXT-09 | budget boundary | 0/1 (manual_review) |
| EXT-10 | incomplete / ambiguous | 1/1 (request_info) |
| EXT-11 | approval threshold (CFO) + budget | 1/1 (manual_review) |

## Failures and guardrail catches

- **rules PUB-01 run 1 (final FAIL)**: next_action manual_review not in ['route_for_approval', 'reuse_existing']
- **rules EXT-03 run 1 (final FAIL)**: next_action manual_review not in ['reuse_existing']
- **rules EXT-04 run 1 (final FAIL)**: next_action manual_review not in ['route_for_approval']
- **rules EXT-06 run 1 (final FAIL)**: next_action manual_review not in ['route_for_approval']
- **rules EXT-09 run 1 (final FAIL)**: next_action manual_review not in ['route_for_approval', 'reuse_existing']
