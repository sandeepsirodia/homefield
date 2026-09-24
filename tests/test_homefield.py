"""Tests map 1:1 to SPEC.md expectations (E1..E9). E10 is a real-agent run, done by hand.

A fixture repo with a scripted history is built per test class: a tiny `calc` module that
grows one function per commit, plus commits that must NOT become tasks.
"""
import glob
import io
import os
import shlex
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import homefield as hf  # noqa: E402

PY = shlex.quote(sys.executable)
TEST_CMD = PY + " -m unittest {files}"

FUNCS = {
    "sub": ("def sub(a, b):\n    return a - b\n", "    def test_sub(self):\n        self.assertEqual(calc.sub(5, 3), 2)\n"),
    "mul": ("def mul(a, b):\n    return a * b\n", "    def test_mul(self):\n        self.assertEqual(calc.mul(4, 3), 12)\n"),
    "div": ("def div(a, b):\n    return a / b\n", "    def test_div(self):\n        self.assertEqual(calc.div(8, 2), 4)\n"),
}
TEST_HEAD = "import unittest\nimport calc\n\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(calc.add(1, 2), 3)\n"


def git(cwd, *args, date=None):
    env = dict(os.environ)
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                    "-c", "core.hooksPath=/dev/null", *args], cwd=cwd, check=True, capture_output=True, env=env)


def make_fixture():
    repo = tempfile.mkdtemp(prefix="hf-fixture-")
    code, tests = "def add(a, b):\n    return a + b\n", TEST_HEAD

    def write(path, text):
        with open(os.path.join(repo, path), "w") as f:
            f.write(text)

    os.makedirs(os.path.join(repo, "tests"))
    write("calc.py", code)
    write("tests/test_calc.py", tests)
    write("README.md", "# calc\n")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Initial calc module with add", date="2026-01-01T10:00:00")

    def feature(name, date):
        nonlocal code, tests
        code += "\n\n" + FUNCS[name][0]
        tests += "\n" + FUNCS[name][1]
        write("calc.py", code)
        write("tests/test_calc.py", tests)
        git(repo, "commit", "-qam", "Add %s function to calc" % name, date=date)

    feature("sub", "2026-01-10T10:00:00")                                      # task 1 (old)
    write("README.md", "# calc\n\nA calculator.\n")
    git(repo, "commit", "-qam", "Describe the project in README", date="2026-02-01T10:00:00")  # docs only
    write("README.md", "# calc\n\nA tiny calculator.\n")
    git(repo, "commit", "-qam", "Reword the README intro", date="2026-03-01T10:00:00")          # docs only
    feature("mul", "2026-07-01T10:00:00")                                      # task 2
    code = code.replace("return a + b", "return b + a")
    tests += "\n    def test_add_zero(self):\n        self.assertEqual(calc.add(0, 0), 0)\n"
    write("calc.py", code)
    write("tests/test_calc.py", tests)
    git(repo, "commit", "-qam", "Refactor add and cover zero", date="2026-07-15T10:00:00")  # tests already pass
    feature("div", "2026-08-01T10:00:00")                                      # task 3
    return repo


# Fake agents: plain shell commands. They "solve" by appending the right function.
SOLVER = """%s -c "
import os, re
p = os.environ['HOMEFIELD_PROMPT']
fn = re.search(r'Add (\\w+) function', p).group(1)
code = {'sub': 'a - b', 'mul': 'a * b', 'div': 'a / b'}
if fn in %%s:
    open('calc.py', 'a').write('\\n\\ndef %%%%s(a, b):\\n    return %%%%s\\n' %%%% (fn, code[fn]))
print('{\\"total_cost_usd\\": 0.5, \\"usage\\": {\\"input_tokens\\": 100, \\"output_tokens\\": 20}}')
"
""" % PY


def solver(solves):
    return "solver=cmd:" + (SOLVER % repr(solves)).strip()


def cli(repo, *argv):
    out = io.StringIO()
    code = hf.main(["--repo", repo, *argv], out=out)
    return code, out.getvalue()


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = make_fixture()
        cli(cls.repo, "mine", "--test-cmd", TEST_CMD)
        cls.tasks = hf.load_tasks(cls.repo)


class TestMine(Base):
    def test_e1_exactly_the_valid_tasks(self):
        msgs = sorted(t["message"] for t in self.tasks)
        self.assertEqual(msgs, ["Add div function to calc", "Add mul function to calc", "Add sub function to calc"])

    def test_e2_reference_patch_passes(self):
        for t in self.tasks:
            with tempfile.TemporaryDirectory() as d:
                hf.export(self.repo, t["commit"], d)
                self.assertEqual(hf.run_tests(d, t["test_cmd"], t["tests"], 60), "pass")

    def test_e3_empty_patch_fails(self):
        for t in self.tasks:
            with tempfile.TemporaryDirectory() as d:
                hf.export(self.repo, t["parent"], d)
                hf.apply_files(self.repo, t["commit"], d, t["tests"])
                self.assertEqual(hf.run_tests(d, t["test_cmd"], t["tests"], 60), "fail")

    def test_e8_since(self):
        tasks = hf.mine(self.repo, TEST_CMD, since="2026-06-01")
        self.assertEqual(sorted(t["message"] for t in tasks), ["Add div function to calc", "Add mul function to calc"])


class TestRun(Base):
    def test_e4_hidden_tests_invisible_to_agent(self):
        spy_out = tempfile.mktemp()
        spy = ("spy=cmd:(git log --all --oneline | wc -l; git log --all -p; grep -r . --include=*.py .) > %s"
               % shlex.quote(spy_out))
        t = next(t for t in self.tasks if "div" in t["message"])
        hf.run_task(self.repo, t, hf.Agent(spy), 60, 60)
        seen = open(spy_out).read()
        self.assertEqual(seen.split("\n", 1)[0].strip(), "1")  # exactly one commit: no future history
        self.assertNotIn("test_div", seen)
        self.assertIn("test_mul", seen)  # sanity: the spy did read the (old) tests

    def test_e5_timeout_then_continue(self):
        code, out = cli(self.repo, "run", "--agent", "sleepy=cmd:sleep 30", "--agent", solver(["sub", "mul", "div"]),
                        "--tasks", "1", "--timeout", "1", "--run-id", "e5")
        results = hf.load_results(self.repo, "e5")
        status = {r["agent"]: r["status"] for r in results}
        self.assertEqual(status, {"sleepy": "timeout", "solver": "solved"})

    def test_e6_report_math(self):
        # v2 changed the default to 3 attempts; pin 1 so this still checks v1's report math exactly
        cli(self.repo, "run", "--agent", solver(["sub", "mul"]), "--run-id", "e6", "--attempts", "1")
        rows, tasks, grid = hf.summarize(hf.load_results(self.repo, "e6"))
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["solved"], r["total"], r["rate"]), (2, 3, 66.7))
        self.assertEqual((r["cost"], r["cost_per_solve"]), (1.5, 0.75))
        md = hf.markdown(hf.load_results(self.repo, "e6"), "calc")
        self.assertIn("| 1 | `solver` | 2/3 | 66.7% | 0s | $1.50 | $0.75 |", md)

    def test_e7_no_leftovers_and_user_repo_untouched(self):
        before_tmp = set(glob.glob(os.path.join(tempfile.gettempdir(), "homefield-*")))
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True).stdout
        cli(self.repo, "run", "--agent", "crasher=cmd:rm -rf calc.py tests; exit 3", "--run-id", "e7")
        after_tmp = set(glob.glob(os.path.join(tempfile.gettempdir(), "homefield-*")))
        self.assertEqual(after_tmp - before_tmp, set())
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.repo, capture_output=True, text=True).stdout
        self.assertEqual(status.strip(), "?? .homefield/")
        self.assertEqual(subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True).stdout, head)
        worktrees = subprocess.run(["git", "worktree", "list"], cwd=self.repo, capture_output=True, text=True).stdout
        self.assertEqual(len(worktrees.strip().splitlines()), 1)
        self.assertTrue(all(r["status"] == "failed" for r in hf.load_results(self.repo, "e7")))

    def test_e9_reproducible(self):
        for rid in ("e9a", "e9b"):
            cli(self.repo, "run", "--agent", solver(["sub", "div"]), "--run-id", rid)
        a = hf.markdown(hf.load_results(self.repo, "e9a"))
        b = hf.markdown(hf.load_results(self.repo, "e9b"))
        self.assertEqual(a, b)

    def test_html_report(self):
        cli(self.repo, "run", "--agent", solver(["sub"]), "--run-id", "zz-html")
        out = os.path.join(tempfile.mkdtemp(), "r.html")
        code, _ = cli(self.repo, "report", "--html", out)
        self.assertEqual(code, 0)
        self.assertIn("<table>", open(out).read())


class TestUnits(unittest.TestCase):
    def test_is_test(self):
        for p in ["tests/test_x.py", "src/foo_test.go", "a/b.test.ts", "x.spec.jsx", "src/__tests__/a.js",
                  "FooTest.java", "spec/models/user_spec.rb"]:
            self.assertTrue(hf.is_test(p), p)
        for p in ["src/testing_utils_impl.py.bak", "src/attest.py", "latest.js", "contest/x.go"]:
            self.assertFalse(hf.is_test(p), p)

    def test_agent_specs(self):
        self.assertEqual(hf.Agent("claude").kind, "claude")
        a = hf.Agent("claude:sonnet")
        self.assertEqual((a.kind, a.arg), ("claude", "sonnet"))
        a = hf.Agent("codex=cmd:codex exec --full-auto \"$HOMEFIELD_PROMPT\"")
        self.assertEqual((a.name, a.kind), ("codex", "cmd"))
        with self.assertRaises(ValueError):
            hf.Agent("gpt")

    def test_claude_is_edit_only_by_default(self):
        seen = {}
        orig = hf.run_capped
        hf.run_capped = lambda cmd, *a, **k: seen.setdefault("cmd", cmd) and (0, "{}", "")
        try:
            hf.Agent("claude").run(tempfile.mkdtemp(), "p", 1)
            self.assertIn("acceptEdits", seen.pop("cmd"))
            hf.Agent("claude", allow_shell=True).run(tempfile.mkdtemp(), "p", 1)
            self.assertIn("--dangerously-skip-permissions", seen["cmd"])
        finally:
            hf.run_capped = orig


if __name__ == "__main__":
    unittest.main()
