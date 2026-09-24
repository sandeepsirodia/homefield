"""Statistics for homefield v2. Standard library only.

Wilson is vendored from lucky (github.com/sandeepsirodia/lucky); the rest is new here.
Every function is checked against independent reference values in tests/test_stats.py.
"""
import math
import random
from statistics import NormalDist

ALPHA = 0.05


def wilson(k, n, conf=0.95):
    if n == 0:
        return 0.0, 1.0
    z = NormalDist().inv_cdf(1 - (1 - conf) / 2)
    p = k / n
    denom, centre = 1 + z * z / n, p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (0.0 if k == 0 else max(0.0, (centre - half) / denom)), (1.0 if k == n else min(1.0, (centre + half) / denom))


def pass_at_k(n, c, k):
    """Unbiased pass@k from n attempts with c correct (Chen et al., 2021): 1 - C(n-c, k) / C(n, k)."""
    if k > n:
        raise ValueError("k must be <= n")
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def mcnemar_exact(b, c):
    """Exact two-sided McNemar test on the discordant counts b (only A solved) and c (only B solved)."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired_permutation(diffs, reps=10000, seed=0):
    """Two-sided sign-flip permutation test on per-task differences (tasks are the unit, which respects
    the correlation between attempts on the same task). Exact enumeration when there are <= 16 tasks."""
    diffs = [d for d in diffs if d != 0]
    n = len(diffs)
    if n == 0:
        return 1.0
    observed = abs(sum(diffs))
    if n <= 16:
        total = extreme = 0
        for mask in range(1 << n):
            s = sum(d if (mask >> i) & 1 else -d for i, d in enumerate(diffs))
            extreme += abs(s) >= observed - 1e-12
            total += 1
        return extreme / total
    rng = random.Random(seed)
    extreme = sum(abs(sum(d if rng.random() < 0.5 else -d for d in diffs)) >= observed - 1e-12 for _ in range(reps))
    return (extreme + 1) / (reps + 1)


def bootstrap_diff_ci(per_task_a, per_task_b, reps=2000, seed=0, conf=0.95):
    """Percentile bootstrap CI for mean(per_task_a) - mean(per_task_b), resampling tasks (paired)."""
    n = len(per_task_a)
    if n == 0:
        return 0.0, 0.0
    rng = random.Random(seed)
    stats = []
    for _ in range(reps):
        idx = [rng.randrange(n) for _ in range(n)]
        stats.append(sum(per_task_a[i] - per_task_b[i] for i in idx) / n)
    stats.sort()
    lo = stats[int((1 - conf) / 2 * reps)]
    hi = stats[min(reps - 1, int((1 + conf) / 2 * reps))]
    return lo, hi


def holm(pvalues):
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted, running = [0.0] * m, 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def compare(outcomes_a, outcomes_b, alpha=ALPHA, reps=10000, ci=True):
    """Paired comparison. outcomes_x: {task_id: [0/1 per attempt]} over the same tasks.
    Returns dict(diff, ci, p, test, significant, better)."""
    tasks = sorted(set(outcomes_a) & set(outcomes_b))
    rate = lambda o, t: sum(o[t]) / len(o[t])  # noqa: E731
    ra, rb = [rate(outcomes_a, t) for t in tasks], [rate(outcomes_b, t) for t in tasks]
    diff = (sum(ra) - sum(rb)) / len(tasks) if tasks else 0.0
    single = all(len(outcomes_a[t]) == 1 and len(outcomes_b[t]) == 1 for t in tasks)
    if single:
        b = sum(1 for t in tasks if outcomes_a[t][0] and not outcomes_b[t][0])
        c = sum(1 for t in tasks if outcomes_b[t][0] and not outcomes_a[t][0])
        p, test = mcnemar_exact(b, c), "McNemar exact"
    else:
        p, test = paired_permutation([x - y for x, y in zip(ra, rb)], reps=reps), "paired permutation over tasks"
    sig = p < alpha
    return {"diff": diff, "ci": bootstrap_diff_ci(ra, rb) if ci else (None, None), "p": p, "test": test, "significant": sig,
            "better": ("a" if diff > 0 else "b") if sig else None, "tasks": len(tasks)}


def verdict(name_a, name_b, cmp):
    lo, hi = cmp["ci"]
    detail = "Δ=%+.0f%%, 95%% CI %+.0f%%…%+.0f%%, p=%s" % (100 * cmp["diff"], 100 * lo, 100 * hi, fmt_p(cmp["p"]))
    if cmp["significant"]:
        win, lose = (name_a, name_b) if cmp["better"] == "a" else (name_b, name_a)
        return "%s beats %s (%s)" % (win, lose, detail)
    return "%s vs %s: no detectable difference (%s)" % (name_a, name_b, detail)


def fmt_p(p):
    return "%.2g" % p if p >= 0.001 else "<0.001"
