"""homefield: benchmarks are away games. Test models on your home field.

Turns your repo's git history into a private benchmark: every commit that changed code *and*
tests becomes a task ("here's the commit message, make the change"), graded by the hidden
tests from that commit. Then ranks coding agents on it.
"""
import argparse
import datetime
import difflib
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
from collections import Counter, defaultdict

import homefield_stats as hs

__version__ = "0.2.0"

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


# ------------------------------------------------------------------ spec quality

def spec_score(message):
    """0..1 heuristic: can an agent know what to do from this commit message alone?
    Vague tasks ("fix") are reported separately so they don't count as model failures."""
    subject, _, body = message.strip().partition("\n")
    words = len(message.split())
    score = 0.3 if words >= 8 else 0.15 if words >= 4 else 0.0
    if re.search(r"`[^`]+`|\b[a-z]+_[a-z_]+\b|\b[a-z]+[A-Z]\w+\b|\b\w+\.\w+\(|\b[\w/]+\.(py|js|ts|go|rs|rb|java)\b", message):
        score += 0.3  # names a function, identifier or file
    if re.search(r"#\d+|https?://", message):
        score += 0.2  # links an issue
    if body.strip():
        score += 0.2  # has an explanation beyond the subject line
    return round(min(1.0, score), 2)


WELL_SPECIFIED = 0.5


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


def mine(repo, test_cmd, since=None, limit=50, max_files=10, test_timeout=300, log=lambda s: None,
         stability_runs=1, stats=None):
    """Mine tasks. With stability_runs=N the empty patch must fail N/N and the real change pass N/N,
    so flaky tests never become tasks. `stats` (a dict) receives counts of why candidates were dropped."""
    tasks, dropped = [], Counter() if stats is None else stats.setdefault("dropped", Counter())
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
            before = [run_tests(d, test_cmd, tests, test_timeout) for _ in range(stability_runs)]
            if all(r == "pass" for r in before):
                log("skip %s: tests already pass without the change" % c["commit"][:8])
                dropped["tests pass without the change"] += 1
                continue
            if any(r != "fail" for r in before):
                log("skip %s: flaky without the change (%s)" % (c["commit"][:8], ",".join(before)))
                dropped["flaky"] += 1
                continue
            apply_files(repo, c["commit"], d, source)
            after = [run_tests(d, test_cmd, tests, test_timeout) for _ in range(stability_runs)]
            if all(r != "pass" for r in after):
                log("skip %s: tests don't pass even with the real change" % c["commit"][:8])
                dropped["reference fails"] += 1
                continue
            if any(r != "pass" for r in after):
                log("skip %s: flaky with the real change (%s)" % (c["commit"][:8], ",".join(after)))
                dropped["flaky"] += 1
                continue
        c.update(id=c["commit"][:10], tests=tests, source=[p for _, p in source], test_cmd=test_cmd,
                 spec_score=spec_score(c["message"]))
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

    def run(self, workdir, prompt, timeout, extra_env=None):
        env = dict(os.environ, HOMEFIELD_PROMPT=prompt, **(extra_env or {}))
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


def run_task(repo, task, agent, timeout, test_timeout, attempt=0, rules=None, variant="base"):
    """rules: optional (relative path, content) written into the snapshot before the agent runs,
    e.g. a CLAUDE.md variant for ablation."""
    with tempfile.TemporaryDirectory(prefix="homefield-run-") as d:
        export(repo, task["parent"], d)
        if rules is not None:
            target = os.path.join(d, rules[0])
            os.makedirs(os.path.dirname(target) or d, exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(rules[1])
        env = {"HOMEFIELD_TASK": task["id"], "HOMEFIELD_ATTEMPT": str(attempt), "HOMEFIELD_VARIANT": variant}
        res = agent.run(d, PROMPT.format(message=task["message"]), timeout, env)
        if res.pop("timeout"):
            status = "timeout"
        else:
            tests = [tuple(t) for t in task["tests"]]
            apply_files(repo, task["commit"], d, tests)  # hidden tests arrive only now
            status = run_tests(d, task["test_cmd"], tests, test_timeout)
            status = {"pass": "solved", "fail": "failed", "timeout": "test-timeout"}[status]
    return dict(res, task=task["id"], agent=agent.name, status=status, attempt=attempt, variant=variant)


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


# ------------------------------------------------------------------ v2: honest statistics

def outcomes(results, agent, variant="base"):
    """{task: [1/0 per attempt]} for one agent (and variant)."""
    out = defaultdict(list)
    for r in sorted(results, key=lambda r: (r["task"], r.get("attempt", 0))):
        if r["agent"] == agent and r.get("variant", "base") == variant:
            out[r["task"]].append(1 if r["status"] == "solved" else 0)
    return dict(out)


def pass_rates(outs):
    """Mean unbiased pass@1 and pass@k (k = attempts, when every task has the same count)."""
    if not outs:
        return None, None, 0
    ns = {len(v) for v in outs.values()}
    p1 = sum(sum(v) / len(v) for v in outs.values()) / len(outs)
    k = ns.pop() if len(ns) == 1 else None
    pk = sum(hs.pass_at_k(len(v), sum(v), k) for v in outs.values()) / len(outs) if k and k > 1 else None
    return p1, pk, k


def v2_sections(results, tasks_by_id=None):
    agents = sorted({r["agent"] for r in results if r.get("variant", "base") == "base"})
    lines = ["", "## Honest numbers", "",
             "| Agent | Tasks | pass@1 | 95% CI | pass@k |", "|---|---|---|---|---|"]
    per_agent = {a: outcomes(results, a) for a in agents}
    for a in agents:
        outs = per_agent[a]
        p1, pk, k = pass_rates(outs)
        zeros = [0.0] * len(outs)
        lo, hi = hs.bootstrap_diff_ci([sum(v) / len(v) for v in outs.values()], zeros)
        lines.append("| `%s` | %d | %.0f%% | %.0f–%.0f%% | %s |" % (
            a, len(outs), 100 * p1, 100 * lo, 100 * hi, "%.0f%% (k=%d)" % (100 * pk, k) if pk is not None else "—"))
    if len(agents) > 1:
        lines += ["", "## Head to head", ""]
        pairs = [(x, y) for i, x in enumerate(agents) for y in agents[i + 1:]]
        cmps = [hs.compare(per_agent[x], per_agent[y]) for x, y in pairs]
        adj = hs.holm([c["p"] for c in cmps]) if len(cmps) > 1 else [c["p"] for c in cmps]
        for (x, y), c, p in zip(pairs, cmps, adj):
            c = dict(c, p=p, significant=p < hs.ALPHA)
            c["better"] = ("a" if c["diff"] > 0 else "b") if c["significant"] else None
            lines.append("- " + hs.verdict("`%s`" % x, "`%s`" % y, c) + " · %s over %d tasks%s" % (
                c["test"], c["tasks"], ", Holm-adjusted" if len(cmps) > 1 else ""))
        if min(len(v) for v in per_agent.values()) < 8:
            lines.append("- ⚠ Fewer than 8 tasks: no difference can reach significance. Mine more tasks.")
    if tasks_by_id:
        vague = sorted(t for t in {r["task"] for r in results}
                       if tasks_by_id.get(t, {}).get("spec_score", 1) < WELL_SPECIFIED)
        if vague:
            lines += ["", "## Well-specified tasks only", "",
                      "%d task(s) have vague commit messages (%s), so a miss there may be the prompt's fault. "
                      "Excluding them:" % (len(vague), ", ".join("`%s`" % v for v in vague)), ""]
            for a in agents:
                outs = {t: v for t, v in per_agent[a].items() if t not in vague}
                p1, _, _ = pass_rates(outs)
                lines.append("- `%s`: pass@1 %s over %d tasks" % (a, "%.0f%%" % (100 * p1) if p1 is not None else "—", len(outs)))
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ v2: contamination probe

PROBE_PROMPT = """This is a commit message from a software repository, and the files the commit changed.
Reproduce the code that the commit added, as exactly as you can. Output only the added lines of code.

Commit message:
{message}

Files changed:
{files}"""


def added_lines(repo, task):
    diff = git(repo, "diff", "-U0", task["parent"], task["commit"], "--", *task["source"])
    return "\n".join(l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))


def probe_similarity(answer, truth):
    norm = lambda s: "\n".join(l.strip() for l in s.strip().splitlines() if l.strip())  # noqa: E731
    return difflib.SequenceMatcher(None, norm(answer), norm(truth)).ratio() if truth.strip() else 0.0


def probe_agent(agent, prompt, timeout):
    """Text-only call: no repo, no tools. Returns the model's answer."""
    if agent.kind == "claude":
        cmd = ["claude", "-p", prompt, "--tools", "", "--output-format", "json"] + (["--model", agent.arg] if agent.arg else [])
        code, out, _ = run_capped(cmd, tempfile.gettempdir(), timeout)
        try:
            return json.loads(out).get("result", "")
        except ValueError:
            return ""
    with tempfile.TemporaryDirectory(prefix="homefield-probe-") as d:
        code, out, _ = run_capped(["sh", "-c", agent.arg], d, timeout, env=dict(os.environ, HOMEFIELD_PROMPT=prompt))
    return out or ""


def cmd_probe(a, out):
    tasks = load_tasks(a.repo)
    agent = Agent(a.agent)
    for t in tasks:
        prompt = PROBE_PROMPT.format(message=t["message"], files="\n".join(t["source"]))
        sim = probe_similarity(probe_agent(agent, prompt, a.timeout), added_lines(a.repo, t))
        t.setdefault("probe", {})[agent.name] = round(sim, 3)
        out.write("%-10s similarity %.2f%s\n" % (t["id"], sim, "  ⚠ possibly memorized" if sim >= a.threshold else ""))
    t_path = os.path.join(a.repo, HOME_DIR, "tasks.jsonl")
    with open(t_path, "w", encoding="utf-8") as f:
        for t in tasks:
            t["memorized"] = any(v >= a.threshold for v in t.get("probe", {}).values())
            f.write(json.dumps(t) + "\n")
    flagged = sum(t["memorized"] for t in tasks)
    out.write("\n%d of %d task(s) flagged as possibly memorized (similarity ≥ %.2f). "
              "Use `run --exclude-memorized` to skip them.\n" % (flagged, len(tasks), a.threshold))
    return 0


# ------------------------------------------------------------------ v2: CLAUDE.md ablation

def split_rules(text, by_section=False):
    """Split an instructions file into removable units. Returns (units, render) where render(skip)
    rebuilds the file without unit index `skip`. Headings and fenced code are kept as structure."""
    lines = text.split("\n")
    blocks, cur, kind, fence = [], [], None, False

    def flush():
        nonlocal cur, kind
        if cur:
            blocks.append((kind, "\n".join(cur)))
        cur, kind = [], None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if not fence:
                flush()
            fence = not fence
            cur.append(line)
            kind = "code"
            if not fence:
                flush()
            continue
        if fence:
            cur.append(line)
            continue
        if re.match(r"#{1,6}\s", stripped):
            flush()
            blocks.append(("h%d" % len(stripped.split()[0]), line))
        elif re.match(r"([-*+]|\d+[.)])\s", stripped) and not (kind == "item" and line[:1] in " \t" and cur):
            flush()
            cur, kind = [line], "item"
        elif not stripped:
            flush()
            blocks.append(("blank", line))
        elif kind in ("item", "para"):
            cur.append(line)
        else:
            flush()
            cur, kind = [line], "para"
    flush()

    if by_section:
        units, groups, current = [], [], []
        for k, b in blocks:
            if k == "h2" and current:
                groups.append(current)
                current = []
            current.append((k, b))
        groups.append(current)
        rule_groups = [g for g in groups if g and g[0][0] == "h2"]
        units = ["\n".join(b for _, b in g).strip() for g in rule_groups]

        def render(skip=None):
            keep = [g for g in groups if not (g and g[0][0] == "h2" and rule_groups.index(g) == skip)]
            return "\n".join(b for g in keep for _, b in g)
        return units, render

    idx = [i for i, (k, _) in enumerate(blocks) if k in ("item", "para")]
    units = [blocks[i][1].strip() for i in idx]

    def render(skip=None):
        drop = idx[skip] if skip is not None else None
        return "\n".join(b for i, (_, b) in enumerate(blocks) if i != drop)
    return units, render


def approx_tokens(text):
    return max(1, round(len(text) / 4))  # ponytail: ~4 chars/token; exact counts need a tokenizer


def ablation_analysis(results_by_variant, n_rules, alpha=hs.ALPHA):
    """results_by_variant: {"base" | "rule-i": {task: [0/1…]}}. Rule i's effect = base - without_i:
    positive means removing the rule hurts (load-bearing), negative means removing it helps (harmful)."""
    base = results_by_variant["base"]
    cmps = [hs.compare(base, results_by_variant["rule-%d" % i]) for i in range(n_rules)]
    adj = hs.holm([c["p"] for c in cmps])
    out = []
    for i, (c, p) in enumerate(zip(cmps, adj)):
        label = "no detectable effect"
        if p < alpha:
            label = "load-bearing" if c["diff"] > 0 else "harmful"
        out.append(dict(c, rule=i, p_adj=p, label=label))
    return out


def run_ablation(tasks, agents, rules_text, attempts, runner, by_section=False):
    """runner(task, agent, attempt, rules_content, variant) -> bool solved. Returns per-agent analysis."""
    units, render = split_rules(rules_text, by_section)
    variants = [("base", render(None))] + [("rule-%d" % i, render(i)) for i in range(len(units))]
    report = {}
    for ag in agents:
        by_var = {}
        for name, content in variants:
            by_var[name] = {t["id"]: [1 if runner(t, ag, k, content, name) else 0 for k in range(attempts)] for t in tasks}
        report[getattr(ag, "name", str(ag))] = ablation_analysis(by_var, len(units))
    return units, report


def ablation_markdown(units, report, path):
    lines = ["# homefield ablate: %s" % path, "",
             "Each rule removed one at a time; effect = solve rate with the rule minus without it. "
             "p-values are Holm-adjusted across rules.", ""]
    for agent, rows in report.items():
        lines += ["## `%s`" % agent, "", "| # | Rule | ~Tokens | Effect of the rule | 95% CI | p (Holm) | Verdict |",
                  "|---|---|---|---|---|---|---|"]
        for r in rows:
            text = units[r["rule"]].replace("\n", " ").replace("|", "\\|")
            lines.append("| %d | %s | %d | %+.0f pts | %+.0f…%+.0f | %s | **%s** |" % (
                r["rule"] + 1, (text[:70] + "…") if len(text) > 70 else text, approx_tokens(units[r["rule"]]),
                100 * r["diff"], 100 * r["ci"][0], 100 * r["ci"][1], hs.fmt_p(r["p_adj"]), r["label"]))
        dead = [r for r in rows if r["label"] == "no detectable effect"]
        lines += ["", "%d of %d rules show no detectable effect (~%d tokens on every turn). That is not proof they do "
                  "nothing: with few tasks, small effects are invisible. See the CIs." % (
                      len(dead), len(rows), sum(approx_tokens(units[r["rule"]]) for r in dead)), ""]
    return "\n".join(lines) + "\n"


def plan(n_tasks, n_agents, attempts, n_variants, per_run_seconds=None, per_run_cost=None):
    runs = n_tasks * n_agents * attempts * n_variants
    return {"runs": runs, "seconds": runs * per_run_seconds if per_run_seconds else None,
            "cost": runs * per_run_cost if per_run_cost is not None else None}


def history_averages(repo):
    try:
        rs = load_results(repo)
    except SystemExit:
        return None, None
    secs = [r["seconds"] for r in rs if r.get("seconds") is not None]
    costs = [r["cost"] for r in rs if r.get("cost") is not None]
    return (sum(secs) / len(secs) if secs else None), (sum(costs) / len(costs) if costs else None)


def print_plan(pl, out):
    out.write("Plan: %d agent runs" % pl["runs"])
    if pl["seconds"]:
        out.write(", ~%.1f hours sequential" % (pl["seconds"] / 3600))
    if pl["cost"] is not None:
        out.write(", ~$%.2f" % pl["cost"])
    out.write("\n")


def cmd_ablate(a, out):
    tasks = [t for t in load_tasks(a.repo) if not (a.exclude_memorized and t.get("memorized"))][:a.tasks or None]
    with open(os.path.join(a.repo, a.rules), encoding="utf-8") as f:
        text = f.read()
    units, _ = split_rules(text, a.by_section)
    agents = [Agent(s.strip(), a.budget, a.allow_shell) for s in a.agent]
    secs, cost = history_averages(a.repo)
    pl = plan(len(tasks), len(agents), a.attempts, len(units) + 1, secs or a.seconds_per_run,
              cost if cost is not None else a.cost_per_run)
    out.write("%d rule(s) in %s, %d task(s), %d agent(s), %d attempt(s) each, %d variants\n" % (
        len(units), a.rules, len(tasks), len(agents), a.attempts, len(units) + 1))
    print_plan(pl, out)
    if len(tasks) < 8:
        out.write("⚠ With fewer than 8 tasks no rule can reach significance.\n")
    if not a.yes:
        out.write("Nothing run. Re-run with --yes to start.\n")
        return 0

    def runner(task, agent, attempt, content, variant):
        r = run_task(a.repo, task, agent, a.timeout, a.test_timeout, attempt, (a.rules, content), variant)
        return r["status"] == "solved"

    units, report = run_ablation(tasks, agents, text, a.attempts, runner, a.by_section)
    md = ablation_markdown(units, report, a.rules)
    out.write(md)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(a.repo, HOME_DIR, "ablate-%s.md" % stamp)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    out.write("Saved: %s (your %s was not changed)\n" % (os.path.relpath(path, a.repo), a.rules))
    return 0


def cmd_plan(a, out):
    tasks = load_tasks(a.repo)
    secs, cost = history_averages(a.repo)
    n_var = 1
    if a.rules:
        with open(os.path.join(a.repo, a.rules), encoding="utf-8") as f:
            n_var = len(split_rules(f.read())[0]) + 1
    print_plan(plan(len(tasks), a.agents, a.attempts, n_var, secs or a.seconds_per_run,
                    cost if cost is not None else a.cost_per_run), out)
    return 0


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
    stats = {}
    tasks = mine(a.repo, test_cmd, a.since, a.limit, a.max_files, a.test_timeout,
                 log=lambda s: out.write(s + "\n") if a.verbose else None, stability_runs=a.stability_runs, stats=stats)
    os.makedirs(os.path.join(a.repo, HOME_DIR), exist_ok=True)
    with open(os.path.join(a.repo, HOME_DIR, "tasks.jsonl"), "w", encoding="utf-8") as f:
        for t in tasks:
            f.write(json.dumps(t) + "\n")
    out.write("Mined %d task(s) into %s/tasks.jsonl\n" % (len(tasks), HOME_DIR))
    if stats.get("dropped"):
        out.write("Dropped: %s\n" % ", ".join("%s %d" % kv for kv in sorted(stats["dropped"].items())))
    return 0 if tasks else 1


def cmd_run(a, out):
    tasks = [t for t in load_tasks(a.repo) if not (a.exclude_memorized and t.get("memorized"))]
    tasks = tasks[:a.tasks] if a.tasks else tasks
    agents = [Agent(s.strip(), a.budget, a.allow_shell) for s in a.agent]
    runs_dir = os.path.join(a.repo, HOME_DIR, "runs")
    run_id = a.run_id
    if a.resume and not run_id:
        existing = sorted(os.listdir(runs_dir)) if os.path.isdir(runs_dir) else []
        run_id = existing[-1][:-len(".jsonl")] if existing else None
    run_id = run_id or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(runs_dir, run_id + ".jsonl")
    os.makedirs(runs_dir, exist_ok=True)
    done = set()
    if a.resume and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for r in map(json.loads, f):
                if "meta" not in r:
                    done.add((r["task"], r["agent"], r.get("attempt", 0), r.get("variant", "base")))
    with open(path, "a" if done else "w", encoding="utf-8") as f:
        if not done:
            f.write(json.dumps({"meta": {"run": run_id, "homefield": __version__, "attempts": a.attempts,
                                         "agents": {ag.name: ag.version() for ag in agents}}}) + "\n")
        for t in tasks:
            for ag in agents:
                for k in range(a.attempts):
                    if (t["id"], ag.name, k, "base") in done:
                        continue
                    r = run_task(a.repo, t, ag, a.timeout, a.test_timeout, attempt=k)
                    f.write(json.dumps(r) + "\n")
                    f.flush()
                    out.write("%-10s %-24s #%d %s\n" % (t["id"], ag.name, k + 1, r["status"]))
    if done:
        out.write("Resumed: skipped %d finished run(s)\n" % len(done))
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
    try:
        tasks_by_id = {t["id"]: t for t in load_tasks(a.repo)}
    except SystemExit:
        tasks_by_id = {}
    out.write(v2_sections(results, tasks_by_id))
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
    m.add_argument("--stability-runs", type=int, default=3,
                   help="run each control N times; keep only tasks that fail N/N before and pass N/N after (default 3)")
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
    r.add_argument("--attempts", type=int, default=3, help="attempts per task and agent (default 3), for pass@k and CIs")
    r.add_argument("--exclude-memorized", action="store_true", help="skip tasks flagged by `homefield probe`")
    r.add_argument("--resume", action="store_true", help="continue the latest (or --run-id) run, skipping finished attempts")
    r.add_argument("--run-id", help="name of the run (default: timestamp)")
    rp = sub.add_parser("report", help="leaderboard for the latest run")
    rp.add_argument("--run-id", help="report a specific run")
    rp.add_argument("--html", help="also write a single-file HTML report")
    pr = sub.add_parser("probe", help="flag tasks the model may have memorized (text-only, no repo access)")
    pr.add_argument("--agent", required=True)
    pr.add_argument("--threshold", type=float, default=0.6, help="similarity to the real diff that flags a task (0-1)")
    pr.add_argument("--timeout", type=int, default=300)
    ab = sub.add_parser("ablate", help="measure which rules in CLAUDE.md / AGENTS.md actually change results")
    ab.add_argument("rules", help="instructions file, relative to the repo (e.g. CLAUDE.md)")
    ab.add_argument("--agent", action="append", required=True)
    ab.add_argument("--attempts", type=int, default=3)
    ab.add_argument("--tasks", type=int, help="only the first N tasks")
    ab.add_argument("--by-section", action="store_true", help="ablate whole ## sections instead of single rules")
    ab.add_argument("--exclude-memorized", action="store_true")
    ab.add_argument("--yes", action="store_true", help="actually run (default: print the plan and cost only)")
    ab.add_argument("--timeout", type=int, default=900)
    ab.add_argument("--test-timeout", type=int, default=300)
    ab.add_argument("--budget", type=float)
    ab.add_argument("--allow-shell", action="store_true")
    ab.add_argument("--seconds-per-run", type=float, help="estimate when there's no previous run to learn from")
    ab.add_argument("--cost-per-run", type=float, help="estimate when there's no previous run to learn from")
    pl = sub.add_parser("plan", help="how many runs, how long, how much, before you spend anything")
    pl.add_argument("--agents", type=int, default=1)
    pl.add_argument("--attempts", type=int, default=3)
    pl.add_argument("--rules", help="also count ablation variants for this instructions file")
    pl.add_argument("--seconds-per-run", type=float)
    pl.add_argument("--cost-per-run", type=float)
    a = p.parse_args(argv)
    a.repo = os.path.abspath(a.repo)
    return {"mine": cmd_mine, "run": cmd_run, "report": cmd_report, "probe": cmd_probe,
            "ablate": cmd_ablate, "plan": cmd_plan}[a.cmd](a, out)


if __name__ == "__main__":
    sys.exit(main())
