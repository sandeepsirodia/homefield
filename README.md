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

## Install

```bash
uv tool install git+https://github.com/sandeepsirodia/homefield   # or: pipx install git+https://…
```

One Python file, zero dependencies. You need `git` and whichever agents you want to pit against each other.

## Why the results are trustworthy

**Every task is proven solvable, and proven to need work.** A commit only becomes a task if its new tests *fail* on the old code and *pass* on the real change. A task nobody could solve, or one that needs no change at all, never makes the list.

**The agent can't peek at the answer.** The obvious approach, a git worktree, shares your object store, so a curious agent could run `git log --all` and read the real fix. homefield instead gives each agent a fresh one-commit snapshot built with `git archive`. The future isn't hidden; it simply isn't there. A test in this repo checks exactly that.

**It dodges memorization.** `--since` keeps only commits newer than your models' training data.

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

<details>
<summary><b>Development</b></summary>

```bash
python -m unittest discover -s tests -v
```

Tests map to [SPEC.md](SPEC.md). They build a fixture repo with a scripted history and use shell-command fake agents, so there are no API calls and no cost. I mutation-tested it: deliberately leaking the hidden tests to the agent makes the suite fail, as it should.

</details>

<p align="center"><sub>MIT © Sandeep Sirodia · Ran it on your repo and got a surprising winner? Open an issue and tell me. A ⭐ helps too.</sub></p>
