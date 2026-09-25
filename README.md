<h1 align="center">homefield</h1>

<p align="center">
  <em>Benchmarks are away games. Test models on your home field.</em>
</p>

<p align="center">
  <a href="https://github.com/sandeepsirodia/homefield/actions/workflows/ci.yml"><img src="https://github.com/sandeepsirodia/homefield/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/dependencies-0-111111?style=flat-square" alt="Zero dependencies">
  <img src="https://img.shields.io/badge/data-stays%20local-111111?style=flat-square" alt="Local-first">
  <img src="https://img.shields.io/badge/license-MIT-111111?style=flat-square" alt="MIT">
</p>

---

A new model drops. The leaderboard says it's 6 points better. Your timeline says it's a game changer.

You switch. And on *your* codebase, with its weird build, its legacy auth module and its tests that need a running Redis, it's… fine? Worse? You honestly can't tell, because you're comparing vibes.

Public benchmarks measure public repos. Models have probably seen those repos. **None of them have seen yours.**

**homefield turns your own git history into a private benchmark.** Every past commit where someone changed code *and* added a test becomes a task: here's the commit message, make the change. The tests from that commit, hidden until the end, decide who passed.

## A real run: Haiku vs Sonnet on simonw/llm

I mined [simonw/llm](https://github.com/simonw/llm)'s 2026 history: **16 real tasks**, each a commit that added a test the old code fails. 6 more candidates were dropped (flaky, or needing dependency changes). Then I ran two Claude models, one attempt per task:

<p align="center"><img src="https://raw.githubusercontent.com/sandeepsirodia/homefield/main/assets/llm-haiku-vs-sonnet.svg" alt="Claude Sonnet and Claude Haiku both solved 7 of 16 tasks; Sonnet cost $0.24 per solved task and Haiku $0.90" width="760"></p>

```console
$ homefield mine --since 2026-02-01 --stability-runs 2
Mined 16 task(s) into .homefield/tasks.jsonl
Dropped: reference fails 5, tests pass without the change 1

$ homefield run --agent claude:haiku --agent claude:sonnet --attempts 1 --budget 1.0
$ homefield report
```

| Rank | Agent | Solved | Pass rate | Median time | Total cost | Cost / solve |
|---|---|---|---|---|---|---|
| 1 | `claude:sonnet` | 7/16 | 43.8% | 23s | $1.65 | $0.24 |
| 2 | `claude:haiku` | 7/16 | 43.8% | 112s | $6.32 | $0.90 |

```
- claude:haiku vs claude:sonnet: no detectable difference (Δ=+0%, 95% CI -19%…+19%, p=1) · McNemar exact over 16 tasks
```

The "cheap" model wasn't cheap. It tied on solve rate, but it took almost 5× longer per attempt and cost almost 4× more per solved task. That's the kind of thing you only learn on your own code.

Caveats, stated plainly: 16 tasks is small (the interval on the difference is ±19 points), there was one attempt each, and agents ran **edit-only**, so they couldn't run the tests themselves, which is part of why fewer than half were solved. Every task, attempt and cost is in [`examples/simonw-llm/`](examples/simonw-llm/).

## New in v2: is it better, or did you get lucky?

From the real run above:

```
## Head to head
- `claude:haiku` vs `claude:sonnet`: no detectable difference (Δ=+0%, 95% CI -19%…+19%, p=1) · McNemar exact over 16 tasks
```

Every comparison now comes with a paired test, a confidence interval and a plain-English verdict. With one attempt per task it's an exact McNemar test on which tasks each agent solved. With several attempts (the default is 3) it's a paired permutation test that treats *tasks* as the unit. The report names the test it used. I measured why that matters by simulating 500 comparisons of two **identical** agents on 30 tasks:

<p align="center"><img src="https://raw.githubusercontent.com/sandeepsirodia/homefield/main/assets/false-alarms.svg" alt="Eyeballing crowns a false winner 11.2% of the time; homefield's verdict 2.2%" width="760"></p>

| Rule for "B is better" | How often it crowns a winner between identical agents |
|---|---|
| Eyeballing: "more than 10 points apart" | **11.2%**, roughly 1 comparison in 9 |
| homefield v2's verdict | **2.2%** |

And the uncomfortable flip side: a *real* 15-point gap (75% vs 60%) on 30 tasks × 3 attempts is detected only **half** the time. Most internal evals are too small to see the differences people argue about. homefield tells you when yours is.

What else v2 adds:
- **Flaky tasks are thrown out.** Each task's controls run 3× (`--stability-runs`). A test that passes sometimes never becomes a task, and the report says how many were dropped.
- **Multiple attempts, honest statistics.** 3 attempts per task by default, unbiased **pass@1 / pass@k**, and comparisons that treat *tasks* as the unit, because attempts on the same task are correlated and counting them separately overstates confidence.
- **Vague tasks are flagged.** A commit message like "fix" can't tell any model what to do. Tasks are scored for how well-specified they are, and results are also reported on the well-specified ones only.
- **A memorization check.** `homefield probe` asks the model to reproduce each commit's code from the message alone, with no repo access. Tasks it can recite are flagged, and `--exclude-memorized` skips them.
- **Resumable.** `--resume` picks up a crashed or interrupted run without redoing finished attempts.

## Which rules in your CLAUDE.md actually do anything?

Everyone's CLAUDE.md grows. Nobody knows which lines matter. So measure it (*illustrative output*):

```console
$ homefield ablate CLAUDE.md --agent claude:sonnet --attempts 3
14 rule(s) in CLAUDE.md, 20 task(s), 1 agent(s), 3 attempt(s) each, 15 variants
Plan: 900 agent runs, ~22.5 hours sequential, ~$270.00
Nothing run. Re-run with --yes to start.
```

It prints the plan and cost first, and nothing runs until you add `--yes`. Then it removes one rule at a time, reruns your tasks, and labels each rule **load-bearing** (removing it hurts), **harmful** (removing it helps), or **no detectable effect**, with Holm-corrected p-values across rules and the ~token cost each rule adds to every single turn. Your CLAUDE.md is never edited; the report is saved to `.homefield/`.

I haven't run a real ablation yet: 15 variants × 20 tasks × 3 attempts is hundreds of dollars, which is why `ablate` prints the plan first. The method is proven by simulation; real results will be linked here.

"No detectable effect" isn't proof a rule is useless, and the report says so. With few tasks, small effects are invisible. The tests prove the method: in a simulated 6-rule file where one rule helps and one hurts, ablation finds exactly those two.

## Install

```bash
uv tool install git+https://github.com/sandeepsirodia/homefield   # or: pipx install git+https://…
```

One Python file, zero dependencies. You need `git` and whichever agents you want to pit against each other.

## Why the results are trustworthy

**Every task is proven solvable, and proven to need work.** A commit only becomes a task if its new tests *fail* on the old code and *pass* on the real change. A task nobody could solve, or one that needs no change at all, never makes the list.

**The agent can't peek at the answer.** The obvious approach, a git worktree, shares your object store, so a curious agent could run `git log --all` and read the real fix. homefield instead gives each agent a fresh one-commit snapshot built with `git archive`. The future isn't hidden; it simply isn't there. A test in this repo checks exactly that.

**It dodges memorization.** `--since` keeps only commits newer than your models' training data, and `homefield probe` flags tasks a model can recite anyway.

**Nothing leaves your machine.** Tasks, hidden tests and results live in `.homefield/`. Only the agents' own API calls go out.

## Bring any agent

| `--agent` | Runs |
|---|---|
| `claude` · `claude:sonnet` · `claude:opus` | Claude Code headless, with cost and tokens read from its output |
| `name=cmd:<any shell command>` | Anything. The prompt is in `$HOMEFIELD_PROMPT` |

```bash
# untested recipes, so check the flags against your CLI version:
--agent 'codex=cmd:codex exec --full-auto "$HOMEFIELD_PROMPT"'
--agent 'gemini=cmd:gemini -p "$HOMEFIELD_PROMPT" --yolo'
--agent 'aider=cmd:aider --yes --message "$HOMEFIELD_PROMPT"'
```

### Safe by default

The Claude adapter is **edit-only** out of the box (`--permission-mode acceptEdits`): it can change files but not run commands. Add `--allow-shell` to let it run tests too. That usually raises solve rates, but only do it inside a container or VM.

### Upgrading from v1

`run` now makes **3 attempts per task by default** (it used to make 1). Pass `--attempts 1` for v1 behavior. Old run files still load, and the v1 report table is unchanged, with the new sections added below it.

## Prior art, and what's new here

- **[SWE-bench](https://www.swebench.com/)** established the method: real issues from real repos, graded by the repo's own tests. homefield applies it to *your* repo.
- **[RepoTrials](https://dev.to/repotrials/repotrials-turn-your-git-history-into-private-coding-agent-benchmarks-4462)** and [commit-replay-bench](https://github.com/Jita81/commit-replay-bench) also turn git history into private benchmarks.
- **[claude-instruction-ablation](https://github.com/evolsb/claude-instruction-ablation)** scores CLAUDE.md rules with a rubric.

What homefield adds:
- snapshots with **no reachable future history**
- **flake-filtered** tasks
- **paired, task-level statistics** with plain verdicts
- a **memorization probe**
- **CLAUDE.md ablation measured by actual solve rates**, with Holm correction and a cost plan up front

<details>
<summary><b>Development</b></summary>

```bash
python -m unittest discover -s tests -v
```

Tests map to [SPEC.md](SPEC.md) (v1) and [SPEC-v2.md](SPEC-v2.md). The statistics are checked against exact enumeration and published formulas, and the verdict's false-alarm rate and power are checked by simulation. They build a fixture repo with a scripted history and use shell-command fake agents, so there are no API calls and no cost. I mutation-tested it: deliberately leaking the hidden tests to the agent makes the suite fail, as it should.

</details>

<p align="center"><sub>MIT © Sandeep Sirodia · Ran it on your repo and got a surprising winner? Open an issue and tell me. A ⭐ helps too.</sub></p>
