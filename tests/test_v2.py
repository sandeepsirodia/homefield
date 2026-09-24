"""v2 expectations (homefield-v2 SPEC E1..E11). E12 = the v1 suite in test_homefield.py still passes.

Statistical claims are checked by simulation with injected runners (no processes, hundreds of repeats);
mechanics are checked end to end on the v1 fixture repo with shell-command fake agents."""
import io
import json
import os
import random
import shlex
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import homefield as hf  # noqa: E402
import homefield_stats as hs  # noqa: E402
from test_homefield import PY, TEST_CMD, cli, git, make_fixture, solver  # noqa: E402


def simulate(p_by_task, attempts, rng):
    return {t: [1 if rng.random() < p else 0 for _ in range(attempts)] for t, p in p_by_task.items()}


class TestHonestComparisons(unittest.TestCase):
    def test_e3_identical_agents_rarely_differ(self):
        # 30 tasks of varying difficulty; both agents have the same per-task probability (attempts correlate
        # within a task, the realistic case). A false "X beats Y" must happen in <= 5% of repeats.
        rng = random.Random(3)
        false_alarms, repeats = 0, 500
        for _ in range(repeats):
            diff = {t: rng.uniform(0.2, 0.95) for t in range(30)}
            a, b = simulate(diff, 3, rng), simulate(diff, 3, rng)
            false_alarms += hs.compare(a, b, reps=400, ci=False)["significant"]
        self.assertLessEqual(false_alarms / repeats, 0.05 + 2 * (0.05 * 0.95 / repeats) ** 0.5)

    def test_e4_real_difference_is_found(self):
        rng = random.Random(4)
        wins, repeats = 0, 200
        for _ in range(repeats):
            a = simulate({t: 0.9 for t in range(30)}, 3, rng)
            b = simulate({t: 0.4 for t in range(30)}, 3, rng)
            c = hs.compare(a, b, reps=400, ci=False)
            wins += c["significant"] and c["better"] == "a"
        self.assertGreaterEqual(wins / repeats, 0.95)

    def test_single_attempt_uses_mcnemar(self):
        a = {t: [1] for t in range(15)}
        b = {t: [1 if t >= 12 else 0] for t in range(15)}
        c = hs.compare(a, b)
        self.assertEqual(c["test"], "McNemar exact")
        self.assertTrue(c["significant"])


RULES = textwrap.dedent("""\
    # Project rules

    - Use tabs for indentation.
    - Keep functions short.
    - RULE-C: run the full test suite before finishing.
    - Prefer composition over inheritance.
    - RULE-E: rewrite every file you touch from scratch.
    - Write docstrings for public functions.
""")


class TestAblation(unittest.TestCase):
    def test_split_rules(self):
        units, render = hf.split_rules(RULES)
        self.assertEqual(len(units), 6)
        self.assertTrue(units[2].startswith("- RULE-C"))
        self.assertNotIn("RULE-C", render(2))
        self.assertIn("# Project rules", render(2))
        self.assertEqual(render(None), RULES.rstrip("\n") + "\n" if RULES.endswith("\n") else RULES)

    def test_split_rules_keeps_code_and_continuations(self):
        text = "## Style\n\n- One rule\n  that wraps.\n\n```sh\nnpm test\n```\n\nA paragraph rule.\n"
        units, render = hf.split_rules(text)
        self.assertEqual(units, ["- One rule\n  that wraps.", "A paragraph rule."])
        self.assertIn("npm test", render(0))
        units, _ = hf.split_rules("## A\nx\n## B\ny\n", by_section=True)
        self.assertEqual(units, ["## A\nx", "## B\ny"])

    def _runner(self, seed):
        rng = random.Random(seed)

        def runner(task, agent, attempt, content, variant):
            p = 0.85 if "RULE-C" in content else 0.3   # rule C is load-bearing
            if "RULE-E" in content:
                p -= 0.45                               # rule E is harmful
            return rng.random() < max(0.05, p)
        return runner

    def test_e6_e7_ablation_finds_the_rules_that_matter(self):
        tasks = [{"id": "t%02d" % i} for i in range(30)]
        units, report = hf.run_ablation(tasks, ["fake"], RULES, 3, self._runner(6))
        labels = {units[r["rule"]].split(":")[0].lstrip("- "): r["label"] for r in report["fake"]}
        self.assertEqual(labels["RULE-C"], "load-bearing")
        self.assertEqual(labels["RULE-E"], "harmful")
        others = [l for k, l in labels.items() if not k.startswith("RULE")]
        self.assertEqual(others, ["no detectable effect"] * 4)
        md = hf.ablation_markdown(units, report, "CLAUDE.md")
        self.assertIn("**load-bearing**", md)
        self.assertIn("**harmful**", md)
        self.assertIn("That is not proof they do nothing", md)


class TestSafety(unittest.TestCase):
    def test_safe_members_rejects_escapes(self):
        import tarfile
        d = tempfile.mkdtemp()
        path = os.path.join(d, "x.tar")
        with tarfile.open(path, "w") as t:
            info = tarfile.TarInfo("../evil.txt")
            info.size = 0
            t.addfile(info, io.BytesIO(b""))
        with tarfile.open(path) as t, self.assertRaises(RuntimeError):
            list(hf.safe_members(t, os.path.join(d, "out")))


class TestMechanics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = make_fixture()
        cli(cls.repo, "mine", "--test-cmd", TEST_CMD, "--stability-runs", "2")
        with open(os.path.join(cls.repo, "CLAUDE.md"), "w") as f:
            f.write("# Rules\n\n- KEY RULE: implement the requested function.\n- Be nice.\n")

    def test_e1_flaky_tasks_are_dropped(self):
        repo = make_fixture()
        counter = tempfile.mktemp()
        flaky = ("import os, unittest\nimport calc\n\nclass T(unittest.TestCase):\n    def test_flaky(self):\n"
                 "        p = %r\n        n = int(open(p).read()) if os.path.exists(p) else 0\n"
                 "        open(p, 'w').write(str(n + 1))\n        self.assertEqual(calc.sq(3), 9)\n"
                 "        self.assertEqual(n %% 2, 0)\n" % counter)
        with open(os.path.join(repo, "calc.py"), "a") as f:
            f.write("\n\ndef sq(x):\n    return x * x\n")
        with open(os.path.join(repo, "tests", "test_flaky.py"), "w") as f:
            f.write(flaky)
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "Add sq function with a flaky test", date="2026-09-01T10:00:00")
        stats = {}
        tasks = hf.mine(repo, TEST_CMD, stability_runs=4, stats=stats)
        self.assertNotIn("Add sq function with a flaky test", [t["message"] for t in tasks])
        self.assertEqual(stats["dropped"]["flaky"], 1)

    def test_e8_ablate_prints_plan_and_runs_nothing(self):
        marker = tempfile.mktemp()
        code, out = cli(self.repo, "ablate", "CLAUDE.md", "--agent", "spy=cmd:touch %s" % marker,
                        "--attempts", "2", "--cost-per-run", "0.5")
        self.assertIn("2 rule(s)", out)
        self.assertIn("Plan: 18 agent runs", out)       # 3 tasks x 1 agent x 2 attempts x 3 variants
        self.assertIn("~$9.00", out)
        self.assertIn("Nothing run", out)
        self.assertFalse(os.path.exists(marker))

    def test_ablate_end_to_end(self):
        key_solver = ("keyed=cmd:grep -q 'KEY RULE' CLAUDE.md && " + solver(["sub", "mul", "div"]).split("cmd:", 1)[1])
        before = open(os.path.join(self.repo, "CLAUDE.md")).read()
        code, out = cli(self.repo, "ablate", "CLAUDE.md", "--agent", key_solver, "--attempts", "2", "--yes")
        self.assertIn("| 1 | - KEY RULE", out)
        self.assertIn("+100 pts", out)                  # removing the key rule makes every attempt fail
        self.assertIn("no detectable effect", out)      # 3 tasks can't prove anything, and it says so
        self.assertEqual(open(os.path.join(self.repo, "CLAUDE.md")).read(), before)
        self.assertTrue([f for f in os.listdir(os.path.join(self.repo, ".homefield")) if f.startswith("ablate-")])

    def test_e9_resume_skips_finished_attempts(self):
        counter = tempfile.mktemp()
        agent = "counted=cmd:echo x >> %s" % shlex.quote(counter)
        cli(self.repo, "run", "--agent", agent, "--attempts", "2", "--run-id", "e9")
        self.assertEqual(len(open(counter).read().split()), 6)
        path = os.path.join(self.repo, ".homefield", "runs", "e9.jsonl")
        lines = open(path).read().splitlines()
        with open(path, "w") as f:                      # simulate a crash after 2 finished runs
            f.write("\n".join(lines[:3]) + "\n")
        code, out = cli(self.repo, "run", "--agent", agent, "--attempts", "2", "--run-id", "e9", "--resume")
        self.assertIn("skipped 2 finished", out)
        self.assertEqual(len(open(counter).read().split()), 10)  # 6 + the 4 missing ones, not 6 more
        self.assertEqual(len(hf.load_results(self.repo, "e9")), 6)

    def test_e10_spec_score(self):
        self.assertLess(hf.spec_score("fix"), hf.WELL_SPECIFIED)
        self.assertLess(hf.spec_score("fix bug"), hf.WELL_SPECIFIED)
        good = "Make `parse_date()` accept ISO week dates\n\nFixes #412: 2026-W05-3 was rejected by the parser."
        self.assertGreaterEqual(hf.spec_score(good), hf.WELL_SPECIFIED)
        tasks = hf.load_tasks(self.repo)
        self.assertTrue(all("spec_score" in t for t in tasks))

    def test_e11_contamination_probe(self):
        repo = make_fixture()
        cli(repo, "mine", "--test-cmd", TEST_CMD, "--stability-runs", "1")
        memorizer = ("parrot=cmd:%s -c \"import os,re; m=re.search(r'Add (\\\\w+) function', os.environ['HOMEFIELD_PROMPT'])."
                     "group(1); ops={'sub':'-','mul':'*','div':'/'}; print(); print(); "
                     "print('def %%s(a, b):' %% m); print('    return a %%s b' %% ops[m])\"" % PY)
        code, out = cli(repo, "probe", "--agent", memorizer)
        self.assertIn("possibly memorized", out)
        tasks = hf.load_tasks(repo)
        self.assertTrue(all(t["memorized"] for t in tasks))
        code, out = cli(repo, "run", "--agent", solver(["sub"]), "--exclude-memorized", "--attempts", "1")
        self.assertNotIn("solved", out)                 # every task was skipped

    def test_report_has_honest_sections(self):
        cli(self.repo, "run", "--agent", solver(["sub", "mul", "div"]), "--agent", solver(["sub"]).replace("solver=", "weak="),
            "--attempts", "2", "--run-id", "zz-report")
        code, out = cli(self.repo, "report", "--run-id", "zz-report")
        self.assertIn("## Honest numbers", out)
        self.assertIn("pass@1", out)
        self.assertIn("## Head to head", out)
        self.assertIn("no detectable difference", out)  # 3 tasks: honest about it
        self.assertIn("Fewer than 8 tasks", out)


if __name__ == "__main__":
    unittest.main()
