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

## What you get

```console
$ homefield mine --since 2026-06-01
Mined 24 task(s) into .homefield/tasks.jsonl

$ homefield run --agent claude:opus --agent claude:sonnet --agent claude:haiku --budget 2
$ homefield report --html report.html
```

*Illustrative numbers. Run it on your repo for real ones:*

| Rank | Agent | Solved | Pass rate | Median time | Total cost | Cost / solve |
|---|---|---|---|---|---|---|
| 1 | `claude:opus` | 19/24 | 79.2% | 212s | $31.40 | $1.65 |
| 2 | `claude:sonnet` | 17/24 | 70.8% | 164s | $9.85 | $0.58 |
| 3 | `claude:haiku` | 11/24 | 45.8% | 71s | $1.92 | $0.17 |

A table like this is what you actually need to know. *Is the top model worth 3× the cost per solved task on our code?* Now you can answer that with data instead of a hunch.

But look again: 19/24 vs 17/24. **Is that a real difference, or a coin flip?** v2 answers that.

## New in v2: is it better, or did you get lucky?

*Illustrative output:*

```
## Head to head
- `claude:sonnet` vs `claude:opus`: no detectable difference (Δ=-8%, 95% CI -21%…+4%, p=0.31) · paired permutation over tasks
```

Every comparison now comes with a paired test, a confidence interval and a plain-English verdict. With one attempt per task it's an exact McNemar test on which tasks each agent solved. With several attempts (the default is 3) it's a paired permutation test that treats *tasks* as the unit. The report names the test it used. I measured why that matters by simulating 500 comparisons of two **identical** agents on 30 tasks:

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
