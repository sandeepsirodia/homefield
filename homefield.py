"""homefield: benchmarks are away games. Test models on your home field.

Turns your repo's git history into a private benchmark: every commit that changed code *and*
tests becomes a task ("here's the commit message, make the change"), graded by the hidden
tests from that commit. Then ranks coding agents on it.
"""
import argparse
import datetime
import html
import json
import os
import re
import shlex
import signal
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time

__version__ = "0.1.0"

HOME_DIR = ".homefield"
TEST_RE = re.compile(
    r"(^|/)(tests?|__tests__|specs?|testing)/|(^|/)test_[^/]+\.py$|_test\.(py|go|rb|exs?)$|"
    r"\.(test|spec)\.[cm]?[jt]sx?$|Tests?\.(java|kt|cs|swift)$|_spec\.rb$")
DOC_RE = re.compile(r"\.(md|rst|txt|adoc)$|(^|/)(docs?|\.github)/|(^|/)(CHANGELOG|LICENSE|AUTHORS)", re.I)
PROMPT = """You are working in a software repository. Make the following change to the code:

{message}

Work autonomously: do not ask questions. Edit the code to implement the change. You may run existing tests."""


# ------------------------------------------------------------------ process helpers

def run_capped(cmd, cwd, timeout, env=None, shell=False):
    """Like subprocess.run, but on timeout kills the whole process group. Agents spawn
    grandchildren that keep pipes open, and a plain timeout would then hang forever."""
    p = subprocess.Popen(cmd, cwd=cwd, env=env, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.communicate()
        return None, "", ""


# ------------------------------------------------------------------ git helpers

def git(repo, *args, check=True, text=True):
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=text)
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr.strip() if text else r.stderr))
    return r.stdout


def is_test(path):
    return bool(TEST_RE.search(path))


def changes(repo, parent, commit):
    """[(status, path)] between two commits, renames split into delete + add."""
    out = []
    for line in git(repo, "diff", "--name-status", "--no-renames", parent, commit).splitlines():
        status, path = line.split("\t", 1)
        out.append((status[0], path))
    return out


def export(repo, commit, dest):
    """Snapshot `commit` into `dest` as a fresh one-commit git repo. No history, so no future leaks."""
    data = subprocess.run(["git", "archive", "--format=tar", commit], cwd=repo, capture_output=True, check=True).stdout
    tmp_tar = os.path.join(dest, ".homefield-snapshot.tar")
    with open(tmp_tar, "wb") as f:
        f.write(data)
    with tarfile.open(tmp_tar) as t:
        try:
            t.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12
            t.extractall(dest)
    os.unlink(tmp_tar)
    git(dest, "init", "-q")
    git(dest, "add", "-A")
    git(dest, "-c", "user.name=homefield", "-c", "user.email=homefield@localhost", "-c", "commit.gpgsign=false",
        "-c", "core.hooksPath=/dev/null",
        "commit", "-qm", "baseline", "--allow-empty")


def apply_files(repo, commit, dest, files):
    """Copy `files` [(status, path)] as they are at `commit` into dest (deleting 'D' ones)."""
    for status, path in files:
        target = os.path.join(dest, path)
        if status == "D":
            if os.path.exists(target):
                os.unlink(target)
            continue
        os.makedirs(os.path.dirname(target) or dest, exist_ok=True)
        blob = subprocess.run(["git", "show", "%s:%s" % (commit, path)], cwd=repo, capture_output=True, check=True).stdout
        with open(target, "wb") as f:
            f.write(blob)


# ------------------------------------------------------------------ tests

def detect_test_cmd(repo):
    has = lambda *names: any(os.path.exists(os.path.join(repo, n)) for n in names)  # noqa: E731
    if has("pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "tox.ini"):
        return "python -m pytest -q {files}"
    if has("package.json"):
        return "npm test --silent"
    if has("go.mod"):
        return "go test ./..."
    if has("Cargo.toml"):
        return "cargo test -q"
    return None


def run_tests(workdir, test_cmd, test_files, timeout):
    files = " ".join(shlex.quote(p) for _, p in test_files if _ != "D")
    cmd = test_cmd.replace("{files}", files)
    code, _, _ = run_capped(cmd, workdir, timeout, shell=True)
    return "timeout" if code is None else "pass" if code == 0 else "fail"


# ------------------------------------------------------------------ mine

def candidate_commits(repo, since=None):
    args = ["log", "--no-merges", "--first-parent", "--format=%H%x00%P%x00%cI%x00%B%x1e"]
    if since:
        args.append("--since=" + since)
    for rec in git(repo, *args).split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, parents, date, message = rec.split("\x00", 3)
        if len(parents.split()) != 1:
            continue  # root commit (or merge)
        yield {"commit": sha, "parent": parents.strip(), "date": date, "message": message.strip()}


def mine(repo, test_cmd, since=None, limit=50, max_files=10, test_timeout=300, log=lambda s: None):
    tasks = []
    for c in candidate_commits(repo, since):
        if len(tasks) >= limit:
            break
        ch = changes(repo, c["parent"], c["commit"])
        tests = [x for x in ch if is_test(x[1])]
        source = [x for x in ch if not is_test(x[1]) and not DOC_RE.search(x[1])]
        if not tests or not source or len(source) > max_files or len(c["message"].split()) < 3:
            continue
        # Negative control: new tests on the old code must fail. Positive control: the real commit must pass.
        with tempfile.TemporaryDirectory(prefix="homefield-mine-") as d:
            export(repo, c["parent"], d)
            apply_files(repo, c["commit"], d, tests)
            if run_tests(d, test_cmd, tests, test_timeout) != "fail":
                log("skip %s: tests already pass without the change" % c["commit"][:8])
                continue
            apply_files(repo, c["commit"], d, source)
            if run_tests(d, test_cmd, tests, test_timeout) != "pass":
                log("skip %s: tests don't pass even with the real change" % c["commit"][:8])
                continue
        c.update(id=c["commit"][:10], tests=tests, source=[p for _, p in source], test_cmd=test_cmd)
        tasks.append(c)
        log("task %s: %s" % (c["id"], c["message"].splitlines()[0][:70]))
    return tasks


# ------------------------------------------------------------------ agents

class Agent:
    """One small interface: run(workdir, prompt, timeout) -> {exit, seconds, cost, tokens}."""

    def __init__(self, spec, budget=None, allow_shell=False):
        self.spec, self.budget, self.allow_shell = spec, budget, allow_shell
        name, _, rest = spec.partition("=")
        if rest.startswith("cmd:"):
            self.name, self.kind, self.arg = name, "cmd", rest[4:]
        elif spec.startswith("cmd:"):
            self.name, self.kind, self.arg = "cmd", "cmd", spec[4:]
        elif spec.split(":")[0] == "claude":
            self.name, self.kind, self.arg = spec, "claude", spec.partition(":")[2] or None
        else:
            raise ValueError("unknown agent %r (use claude, claude:<model>, or name=cmd:<shell command>)" % spec)

    def version(self):
        if self.kind == "claude":
            try:
                return subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
            except OSError:
                return "claude (not installed)"
        return self.arg

    def run(self, workdir, prompt, timeout):
        env = dict(os.environ, HOMEFIELD_PROMPT=prompt)
        if self.kind == "claude":
            # Safe default: the agent may edit files but not run commands. --allow-shell lifts that.
            perms = ["--dangerously-skip-permissions"] if self.allow_shell else ["--permission-mode", "acceptEdits"]
            cmd = ["claude", "-p", prompt, "--output-format", "json", *perms]
            if self.arg:
                cmd += ["--model", self.arg]
            if self.budget:
                cmd += ["--max-budget-usd", str(self.budget)]
        else:
            cmd = ["sh", "-c", self.arg]
        start = time.time()
        try:
            code, stdout, _ = run_capped(cmd, workdir, timeout, env=env)
        except OSError as e:
            code, stdout = 127, json.dumps({"error": str(e)})
        res = {"exit": code, "seconds": time.time() - start, "cost": None, "tokens": None, "timeout": code is None}
        # Agents (and fake agents in tests) can report usage as a JSON object on stdout.
        try:
            data = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
            res["cost"] = data.get("total_cost_usd", data.get("cost"))
            u = data.get("usage") or {}
            res["tokens"] = (u.get("input_tokens", 0) + u.get("output_tokens", 0)
                             + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)) or data.get("tokens")
        except (ValueError, AttributeError, IndexError):
            pass
        return res


def run_task(repo, task, agent, timeout, test_timeout):
    with tempfile.TemporaryDirectory(prefix="homefield-run-") as d:
        export(repo, task["parent"], d)
        res = agent.run(d, PROMPT.format(message=task["message"]), timeout)
        if res.pop("timeout"):
            status = "timeout"
        else:
            tests = [tuple(t) for t in task["tests"]]
            apply_files(repo, task["commit"], d, tests)  # hidden tests arrive only now
            status = run_tests(d, task["test_cmd"], tests, test_timeout)
            status = {"pass": "solved", "fail": "failed", "timeout": "test-timeout"}[status]
    return dict(res, task=task["id"], agent=agent.name, status=status)


# ------------------------------------------------------------------ report

def summarize(results):
    agents = sorted({r["agent"] for r in results})
    tasks = sorted({r["task"] for r in results})
    rows = []
    for a in agents:
        rs = [r for r in results if r["agent"] == a]
        solved = sum(r["status"] == "solved" for r in rs)
        costs = [r["cost"] for r in rs if r["cost"] is not None]
        total = round(sum(costs), 2) if costs else None
        rows.append({
            "agent": a, "solved": solved, "total": len(rs),
            "rate": round(100.0 * solved / len(rs), 1) if rs else 0.0,
            "median_s": round(statistics.median(r["seconds"] for r in rs)) if rs else 0,
            "cost": total,
            "cost_per_solve": round(total / solved, 2) if total is not None and solved else None,
        })
    rows.sort(key=lambda x: (-x["solved"], x["cost"] if x["cost"] is not None else 1e9, x["agent"]))
    grid = {(r["task"], r["agent"]): r["status"] for r in results}
    return rows, tasks, grid


MARK = {"solved": "✅", "failed": "❌", "timeout": "⏱", "test-timeout": "⏱"}


def money(x):
    return "—" if x is None else "$%.2f" % x


def markdown(results, repo_name=""):
    rows, tasks, grid = summarize(results)
    out = ["# homefield results%s" % (": " + repo_name if repo_name else ""), "",
           "| Rank | Agent | Solved | Pass rate | Median time | Total cost | Cost / solve |",
           "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        out.append("| %d | `%s` | %d/%d | %.1f%% | %ds | %s | %s |" % (
            i, r["agent"], r["solved"], r["total"], r["rate"], r["median_s"], money(r["cost"]), money(r["cost_per_solve"])))
    out += ["", "## Per task", "", "| Task | " + " | ".join("`%s`" % r["agent"] for r in rows) + " |",
            "|---|" + "---|" * len(rows)]
    for t in tasks:
        out.append("| `%s` | " % t + " | ".join(MARK.get(grid.get((t, r["agent"])), " ") for r in rows) + " |")
    return "\n".join(out) + "\n"


def html_report(results, repo_name=""):
    rows, tasks, grid = summarize(results)
    th = "".join("<th>%s</th>" % html.escape(r["agent"]) for r in rows)
    body = "".join("<tr><td>%d</td><td><b>%s</b></td><td>%d/%d</td><td>%.1f%%</td><td>%ds</td><td>%s</td><td>%s</td></tr>" % (
        i, html.escape(r["agent"]), r["solved"], r["total"], r["rate"], r["median_s"], money(r["cost"]), money(r["cost_per_solve"]))
        for i, r in enumerate(rows, 1))
    cells = "".join("<tr><td><code>%s</code></td>%s</tr>" % (t, "".join(
        "<td>%s</td>" % MARK.get(grid.get((t, r["agent"])), "") for r in rows)) for t in tasks)
    return """<!doctype html><meta charset=utf-8><title>homefield results</title>
<style>body{font:15px system-ui;max-width:900px;margin:40px auto;padding:0 16px;color:#1a1a1a;background:#fff}
table{border-collapse:collapse;width:100%%;margin:16px 0}td,th{padding:6px 10px;border-bottom:1px solid #ddd;text-align:left}
@media(prefers-color-scheme:dark){body{background:#111;color:#eee}td,th{border-color:#333}}</style>
<h1>homefield results%s</h1><table><tr><th>#</th><th>Agent</th><th>Solved</th><th>Pass rate</th><th>Median</th><th>Cost</th><th>Cost/solve</th></tr>%s</table>
<h2>Per task</h2><table><tr><th>Task</th>%s</tr>%s</table>""" % (
        html.escape(": " + repo_name) if repo_name else "", body, th, cells)


# ------------------------------------------------------------------ CLI

def load_tasks(repo):
    path = os.path.join(repo, HOME_DIR, "tasks.jsonl")
    if not os.path.exists(path):
        raise SystemExit("no tasks yet: run `homefield mine` first")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def cmd_mine(a, out):
    test_cmd = a.test_cmd or detect_test_cmd(a.repo)
    if not test_cmd:
        raise SystemExit("can't detect how to run tests; pass --test-cmd 'python -m pytest -q {files}'")
    tasks = mine(a.repo, test_cmd, a.since, a.limit, a.max_files, a.test_timeout,
                 log=lambda s: out.write(s + "\n") if a.verbose else None)
    os.makedirs(os.path.join(a.repo, HOME_DIR), exist_ok=True)
    with open(os.path.join(a.repo, HOME_DIR, "tasks.jsonl"), "w", encoding="utf-8") as f:
        for t in tasks:
            f.write(json.dumps(t) + "\n")
    out.write("Mined %d task(s) into %s/tasks.jsonl\n" % (len(tasks), HOME_DIR))
    return 0 if tasks else 1


def cmd_run(a, out):
    tasks = load_tasks(a.repo)[:a.tasks] if a.tasks else load_tasks(a.repo)
    agents = [Agent(s.strip(), a.budget, a.allow_shell) for s in a.agent]
    run_id = a.run_id or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(a.repo, HOME_DIR, "runs", run_id + ".jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"meta": {"run": run_id, "homefield": __version__,
                                     "agents": {ag.name: ag.version() for ag in agents}}}) + "\n")
        for t in tasks:
            for ag in agents:
                r = run_task(a.repo, t, ag, a.timeout, a.test_timeout)
                f.write(json.dumps(r) + "\n")
                f.flush()
                out.write("%-10s %-24s %s\n" % (t["id"], ag.name, r["status"]))
    out.write("\nResults: %s\nNext: homefield report\n" % os.path.relpath(path, a.repo))
    return 0


def load_results(repo, run_id=None):
    d = os.path.join(repo, HOME_DIR, "runs")
    runs = sorted(os.listdir(d)) if os.path.isdir(d) else []
    if not runs:
        raise SystemExit("no runs yet: run `homefield run` first")
    name = (run_id + ".jsonl") if run_id else runs[-1]
    with open(os.path.join(d, name), encoding="utf-8") as f:
        return [r for r in map(json.loads, f) if "meta" not in r]


def cmd_report(a, out):
    results = load_results(a.repo, a.run_id)
    name = os.path.basename(os.path.abspath(a.repo))
    out.write(markdown(results, name))
    if a.html:
        with open(a.html, "w", encoding="utf-8") as f:
            f.write(html_report(results, name))
        out.write("\nHTML report: %s\n" % a.html)
    return 0


def main(argv=None, out=None):
    out = out or sys.stdout
    p = argparse.ArgumentParser(prog="homefield", description="Benchmark coding agents on your own repo's history.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--repo", default=".", help="git repository (default: .)")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mine", help="find commits that make good tasks")
    m.add_argument("--test-cmd", help="how to run tests; {files} = the task's test files (default: auto-detect)")
    m.add_argument("--since", help="only commits after this date, e.g. 2026-06-01 (avoid training-data contamination)")
    m.add_argument("--limit", type=int, default=50, help="max tasks (default 50)")
    m.add_argument("--max-files", type=int, default=10, help="max source files changed per task (default 10)")
    m.add_argument("--test-timeout", type=int, default=300)
    m.add_argument("-v", "--verbose", action="store_true")
    r = sub.add_parser("run", help="run agents on the mined tasks")
    r.add_argument("--agent", action="append", required=True,
                   help="claude | claude:<model> | name=cmd:<shell command> (prompt in $HOMEFIELD_PROMPT). Repeatable.")
    r.add_argument("--tasks", type=int, help="only the first N tasks")
    r.add_argument("--timeout", type=int, default=900, help="seconds per agent attempt (default 900)")
    r.add_argument("--budget", type=float, help="max USD per attempt (claude: --max-budget-usd)")
    r.add_argument("--allow-shell", action="store_true",
                   help="let the claude agent run any command (bypasses permissions; use inside a container)")
    r.add_argument("--test-timeout", type=int, default=300)
    r.add_argument("--run-id", help=argparse.SUPPRESS)
    rp = sub.add_parser("report", help="leaderboard for the latest run")
    rp.add_argument("--run-id", help="report a specific run")
    rp.add_argument("--html", help="also write a single-file HTML report")
    a = p.parse_args(argv)
    a.repo = os.path.abspath(a.repo)
    return {"mine": cmd_mine, "run": cmd_run, "report": cmd_report}[a.cmd](a, out)


if __name__ == "__main__":
    sys.exit(main())
