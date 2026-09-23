# homefield

**Benchmarks are away games. Test models on your home field.**

SWE-bench says model A beats model B. On *your* codebase, with *your* conventions, *your* test setup, and *your* weird build? Nobody knows, until now.

homefield turns your repo's git history into a private benchmark. Every past commit that changed code **and** added tests becomes a task: *"here's the commit message, make the change."* The hidden tests from that commit grade the result. Then it ranks agents and models on solve rate, time, and cost per solved task.

```console
$ homefield mine --since 2026-06-01
Mined 24 task(s) into .homefield/tasks.jsonl

$ homefield run --agent claude:opus --agent claude:sonnet --agent claude:haiku --budget 2
$ homefield report --html report.html
```

*Example report (illustrative numbers; run it on your repo for real ones):*

| Rank | Agent | Solved | Pass rate | Median time | Total cost | Cost / solve |
|---|---|---|---|---|---|---|
| 1 | `claude:opus` | 19/24 | 79.2% | 212s | $31.40 | $1.65 |
| 2 | `claude:sonnet` | 17/24 | 70.8% | 164s | $9.85 | $0.58 |
| 3 | `claude:haiku` | 11/24 | 45.8% | 71s | $1.92 | $0.17 |

## Install

```bash
uv tool install git+https://github.com/sandeepsirodia/homefield   # or: pipx install git+https://…
```

One Python file, zero dependencies. Needs `git` and whichever agent CLIs you want to compare.

## How it works

**`homefield mine`** walks history (first-parent, no merges) and keeps a commit only if:

1. it touches source files **and** test files (docs/changelog-only commits are ignored),
2. the new tests **fail** on the parent commit (negative control, so the task actually requires work), and
3. the new tests **pass** on the commit itself (positive control, so the task is solvable).

`--since` keeps only recent commits, ideally after your models' training cutoff, so they can't have memorized the answer.

**`homefield run`** gives each agent a clean snapshot of the parent commit and the commit message as the prompt. The snapshot is a fresh one-commit repo built with `git archive`, **not a worktree**. A worktree shares your object store, and the agent could `git log --all` its way to the answer. Afterwards the hidden tests are dropped in and run. Each attempt has a time limit (`--timeout`) and, for Claude, a spend cap (`--budget` → `--max-budget-usd`).

**`homefield report`** prints a Markdown leaderboard + per-task grid; `--html` writes a single-file report you can share.

## Agents

| `--agent` | What runs |
|---|---|
| `claude` / `claude:<model>` | Claude Code headless (`claude -p`), with cost and tokens parsed from its JSON output |
| `name=cmd:<shell command>` | Anything. The prompt is in `$HOMEFIELD_PROMPT`, cwd is the task snapshot |

```bash
# Codex CLI, Gemini CLI, aider, your own agent (untested recipes: check flags against your CLI version):
--agent 'codex=cmd:codex exec --full-auto "$HOMEFIELD_PROMPT"'
--agent 'gemini=cmd:gemini -p "$HOMEFIELD_PROMPT" --yolo'
--agent 'aider=cmd:aider --yes --message "$HOMEFIELD_PROMPT"'
```

If a `cmd:` agent prints a JSON object like `{"total_cost_usd": 0.42, "usage": {...}}` as its last line, homefield records the cost.

### Safety

The built-in Claude adapter runs **edit-only** by default (`--permission-mode acceptEdits`): it can change files but not run commands. `--allow-shell` gives it full permissions (`--dangerously-skip-permissions`), which usually raises solve rates because it can run tests. Only use that inside a container or VM. The snapshot is a temp directory, but a shell-enabled agent can reach anything your user can.

## Everything stays local

Tasks, hidden tests, and results live in `.homefield/` (add it to `.gitignore`). Nothing is uploaded; only the agents' own API calls leave your machine.

## Development

```bash
python -m unittest discover -s tests -v
```

Tests map to the expectations in [SPEC.md](SPEC.md). They build a fixture repo with a scripted history and use shell-command fake agents, so no API calls and no cost. Includes a check that the agent cannot see the hidden tests, even through git history.

MIT © Sandeep Sirodia
