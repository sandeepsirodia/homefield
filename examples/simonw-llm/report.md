# homefield results: simonw_llm

| Rank | Agent | Solved | Pass rate | Median time | Total cost | Cost / solve |
|---|---|---|---|---|---|---|
| 1 | `claude:sonnet` | 7/16 | 43.8% | 23s | $1.65 | $0.24 |
| 2 | `claude:haiku` | 7/16 | 43.8% | 112s | $6.32 | $0.90 |

## Per task

| Task | `claude:sonnet` | `claude:haiku` |
|---|---|---|
| `0b51c616ea` | ❌ | ❌ |
| `196d5a803b` | ❌ | ❌ |
| `307bd0e540` | ❌ | ✅ |
| `3b9dd7714b` | ✅ | ❌ |
| `508b4f8bd7` | ❌ | ❌ |
| `733138bc5f` | ❌ | ❌ |
| `73d6e38a81` | ❌ | ❌ |
| `80b42caaf4` | ✅ | ✅ |
| `84d3722e7b` | ✅ | ✅ |
| `8eff21758b` | ✅ | ✅ |
| `c84cab50fa` | ❌ | ❌ |
| `c9ccd3fcf8` | ❌ | ❌ |
| `d2023f1113` | ✅ | ✅ |
| `e215c42112` | ❌ | ❌ |
| `ef69dfb9e0` | ✅ | ✅ |
| `f947edd5d7` | ✅ | ✅ |

## Honest numbers

| Agent | Tasks | pass@1 | 95% CI | pass@k |
|---|---|---|---|---|
| `claude:haiku` | 16 | 44% | 19–69% | — |
| `claude:sonnet` | 16 | 44% | 19–69% | — |

## Head to head

- `claude:haiku` vs `claude:sonnet`: no detectable difference (Δ=+0%, 95% CI -19%…+19%, p=1) · McNemar exact over 16 tasks
