# Offline context-retention ablation

| Case | Full-history JSON chars | Bounded JSON chars | Required information / block |
|---|---:|---:|---|
| short_task | 364 | 364 | 3/3 |
| long_source_history | 140243 | 10624 | 4/4 |
| partial_and_corrected_facts | 140751 | 11132 | 6/6 |
| evaluated_study_with_retry_error | 140276 | 10657 | 5/5 |
| essential_overflow | 9329 | 0 | blocked/expected |

No model was called. These are message-character and exact-retention checks, not token cost or reasoning accuracy.
Negative controls deliberately drop a correction or rewrite a scientific statement and must be rejected.
