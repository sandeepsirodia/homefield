# homefield — SPEC

> Benchmarks are away games. Test models on your home field.

Turns your repo's own git history into a private benchmark, then ranks coding agents/models (Claude Code, Codex, Gemini CLI, …) on it. The answer to "which model is actually best for *my* codebase?"

## Who it's for
Teams choosing or paying for an AI coding agent; engineers who distrust public leaderboards (contamination, benchmark overfitting).

## Must have (v1)
1. **`homefield mine`**: scans git history for good tasks: commits that change source files **and** tests, where the tests fail at the parent commit and pass at the commit. Writes `.homefield/tasks.jsonl`.
   - Filters: `--since <date>` (to avoid training-data contamination), max diff size, language.
2. **`homefield run --agent claude,codex --tasks N`**: for each task and agent:
   - Exports the parent commit into a fresh one-commit repo (`git archive`, not a worktree, because a worktree shares the object store and would leak the answer), with the task's test changes **hidden**.
   - Gives the agent the prompt (the commit message). Pulling linked issue text is v2.
   - Enforces a time limit and a spend limit per task.
   - Applies the hidden tests and runs them.
3. **`homefield report`**: pass rate, median time, cost per solved task, and per-task grid. Output as Markdown + a single HTML file.
4. **Agent adapters** behind one small interface (`run(workdir, prompt, timeout) -> {exit, tokens, cost, seconds}`). v1 ships a built-in `claude` adapter (edit-only by default; `--allow-shell` opts into full permissions) plus a generic `name=cmd:<shell>` adapter that covers Codex, Gemini CLI, aider, etc.
5. **Local-first:** tasks, hidden tests, and results stay in `.homefield/`. Only the agents' own API calls leave the machine.
6. **Reproducible:** a fixed task set file plus a recorded agent version per run.

## Won't do (v1)
- Hosted leaderboard / SaaS.
- LLM-judged grading. Only the tests decide.
- Tasks without tests.

## Expectations → test cases
Fixtures: `tests/fixture-repos/` holds tiny git repos (Python + JS) with scripted histories, created by a setup script so they're deterministic.

| ID | Given | When | Then |
|---|---|---|---|
| E1 | Fixture repo with 3 valid task commits + 2 docs-only commits + 1 commit whose tests already passed at the parent | `mine` | Exactly the 3 valid tasks are found |
| E2 | A mined task | Grade the **reference patch** (the real commit) | Passes (positive control) |
| E3 | A mined task | Grade an **empty patch** | Fails (negative control). A task that passes with no changes is discarded |
| E4 | Agent workdir during a run | Inspect the files | Hidden test changes are not present anywhere the agent can read (incl. `.git` objects reachable from HEAD) |
| E5 | Fake agent adapter that sleeps past the timeout | `run` | Task marked `timeout`; run continues with the next task |
| E6 | Fake adapter returning scripted results (2/3 solved, known costs) | `report` | Pass rate 66.7%, correct cost-per-solve, deterministic Markdown (snapshot test) |
| E7 | Any run, including crashes | After the run | No leftover worktrees; user's working tree and branches untouched |
| E8 | `--since 2026-06-01` | `mine` | No task older than that date |
| E9 | Same task file + same fake adapter | Run twice | Identical reports (reproducibility) |
| E10 | Real run on 1 public repo, 2 real agents | `run` + `report` | Completes end to end; results published in the README as the demo |

## Done when
- E1–E10 pass (E10 is a manual/nightly run, not in CI).
- README headline: a real result on a well-known OSS repo ("On <repo>, Claude solved X/20, Codex Y/20, at $Z/solve").
