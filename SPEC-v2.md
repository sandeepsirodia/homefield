# homefield v2 — SPEC

> Is the new model actually better on your code, or did you get lucky?

v1 turned git history into a private benchmark. v2 makes its answers **statistically honest**, and adds the feature everyone needs: **measuring which rules in your CLAUDE.md actually matter.**

Builds on `github.com/sandeepsirodia/homefield` (v1: mining, `git archive` snapshots, hidden tests, reports). Everything in v1's SPEC still holds.

## Who it's for
- Teams deciding between models or agents on real cost/quality numbers.
- Anyone whose CLAUDE.md or AGENTS.md has grown to 300 lines of rules nobody can prove help.

## Must have (v2)
1. **Flake filter at mining time:** run each reference solution and each empty patch `--stability-runs 5` times. Keep only tasks where the reference passes 5/5 and the empty patch fails 5/5. Report how many tasks were dropped for flakiness.
2. **k attempts per task:** `--attempts k` (default 3). Report **pass@1** (the unbiased estimator) and **pass@k**.
3. **Honest comparisons:** a **paired** analysis per task (the same tasks for every agent):
   - a paired bootstrap 95% interval on the solve-rate difference
   - an exact **McNemar** test on per-task outcomes
   - a verdict in plain words, reusing `lucky`'s verdict logic (vendored, one file). For example: `sonnet vs opus: no detectable difference (Δ=+4%, 95% CI −8%…+15%)`
4. **Spec-quality score per task:** a heuristic (message length, whether it names the file or function, whether an issue is linked). Report results separately for well-specified tasks, so a vague commit message doesn't count as a model failure.
5. **Contamination probe (opt-in):** give the model only the commit message and file path, ask it to reproduce the diff, and measure similarity to the real diff. Tasks above a threshold are flagged as "possibly memorized" and can be excluded with `--exclude-memorized`.
6. **`homefield ablate CLAUDE.md`:**
   - split the file into rules (Markdown list items and paragraphs; each `##` section can instead be one unit with `--by-section`)
   - run the baseline (full file) and one variant per removed rule, same tasks, k attempts each
   - report each rule's effect on solve rate with a CI, plus its token cost per turn
   - rank rules: `load-bearing`, `no detectable effect`, `harmful`
   - output a suggested slimmer file, never auto-applied
7. **Budget planner:** `homefield plan` prints expected runs, time and dollars before anything runs (from v1 per-task averages or a `--cost-per-run` estimate). Ablation over 30 rules is expensive, and the user must see that first.
8. **Resumable runs:** crash or Ctrl-C and `homefield run --resume` skips finished (task, agent, attempt) triples.
9. Still one file plus the vendored stats module, stdlib only.

## Won't do (v2)
- Ablating combinations of rules (one-at-a-time only; interactions are v3).
- Automatically rewriting CLAUDE.md.
- A hosted leaderboard.

## Expectations → test cases
Fake agents are shell scripts with controllable per-task success probabilities, as in v1.

| ID | Given | When | Then |
|---|---|---|---|
| E1 | A task whose test passes 3/5 on the reference | Mining with `--stability-runs 5` | Dropped, counted under "flaky" |
| E2 | n=10 attempts, c=3 correct | pass@1, pass@5 | Match the unbiased estimator 1 − C(n−c,k)/C(n,k) exactly |
| E3 | Two fake agents with identical success probability (0.6) over 30 tasks, k=3, repeated 500 times | Verdict | "No detectable difference" in ≥ 95% of repeats (false-positive control) |
| E4 | Fake agents at 0.9 vs 0.4 | Verdict | "A is better" in ≥ 95% of repeats (power) |
| E5 | A known 2×2 discordant table (b=12, c=3) | McNemar exact | p matches the binomial reference to 4 decimal places |
| E6 | A CLAUDE.md of 6 rules where the fake agent's success depends on the presence of rule 3 only | `ablate` | Rule 3 is `load-bearing`; the other 5 show `no detectable effect` |
| E7 | A fake agent that fails more when rule 5 is present | `ablate` | Rule 5 is `harmful` |
| E8 | `ablate` on 30 rules | `plan` | Printed cost estimate equals runs × per-run estimate; nothing runs without `--yes` |
| E9 | A run killed halfway | `--resume` | Completes; finished triples are not rerun (checked by fake-agent invocation count) |
| E10 | A task whose commit message is "fix" | Spec score | Low; excluded from the "well-specified" report |
| E11 | A fake model that reproduces the exact diff from the message alone | Contamination probe | Task flagged as memorized |
| E12 | All v1 tests | Run | Still pass (no regressions) |

## Launch number
1. Run `homefield ablate` on a real, widely shared CLAUDE.md (with the author's permission) or on your own. Publish which rules are load-bearing, which do nothing, and the token cost of the dead ones.
2. Separately, run 2–3 models on a real OSS repo with k=3 and publish the paired verdicts, including every "no detectable difference".

## Done when
E1–E12 pass. The README headline is an ablation result on a real CLAUDE.md, with its confidence intervals shown.
