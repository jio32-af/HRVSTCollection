#!/usr/bin/env python3
"""HRVSTATSP paper benchmarks: our solver vs NN / 2-Opt / Simulated Annealing.

Scope (user-settled): Euclidean symmetric instances ONLY, 100 seeded
instances per problem size N = 10, 15, 25, 50 (unit square, same generator
style as TSP.build_instance). Our solver is Euclidean-native
(solve_staged(coords, cand, eu, ev)) and cannot consume cost matrices, so it
is timed through its real pipeline (candidate build + staged solve) on the
same coordinates the baselines' distance matrix is derived from.

Defaults (agent-chosen, documented):
  - NN: classic single-start (city 0), order of the matrix.
  - 2-Opt: best-improvement until local optimum, seeded from the NN tour.
  - SA: 2-opt moves, geometric cooling T0=0.5 -> T1=1e-5,
    iters = 150*n*n, seeded per trial, seeded from the NN tour.
  - Reference: exact Held-Karp length for N = 10, 15; best-known tour
    (min over the 4 methods) for N = 25, 50.
  - Timing: per-method solve work only. Shared Euclidean matrix build is
    untimed; ours includes candidate/edge prep (integral to the solver).

Outputs (all under paper/):
  results.csv, fig1_optimality_gap.png, fig2_pareto_frontier.png (300 DPI).
"""
from __future__ import annotations

import csv
import os
import sys
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import numba

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import TSP as solver  # noqa: E402  (our HRVSTATSP solver)

SIZES = [10, 15, 25, 50]
TRIALS = 100
METHODS = ["hrvst", "nearest_neighbor", "two_opt", "simulated_annealing"]
SA_ITERS_PER_N2 = 150
SA_T0, SA_T1 = 0.5, 1e-5

COLORS = {
    "hrvst": "#e8a33d",
    "nearest_neighbor": "#1f77b4",
    "two_opt": "#2ca02c",
    "simulated_annealing": "#d62728",
}
LABELS = {
    "hrvst": "HRVST (ours)",
    "nearest_neighbor": "Nearest Neighbor",
    "two_opt": "Standard 2-Opt",
    "simulated_annealing": "Simulated Annealing",
}
MARKERS = {"hrvst": "o", "nearest_neighbor": "s", "two_opt": "^", "simulated_annealing": "D"}
ANNOTATE_XY = {
    "hrvst": (4, -11),
    "nearest_neighbor": (4, 6),
    "two_opt": (4, 6),
    "simulated_annealing": (4, 6),
}


# ----------------------------------------------------------------------------
# Numba kernels for baselines + Held-Karp
# ----------------------------------------------------------------------------
@numba.njit(fastmath=True)
def tour_len_matrix(D, tour):
    n = tour.shape[0]
    s = 0.0
    for i in range(n - 1):
        s += D[tour[i], tour[i + 1]]
    return s + D[tour[n - 1], tour[0]]


@numba.njit(fastmath=True)
def nn_tour(D):
    n = D.shape[0]
    tour = np.empty(n, dtype=np.int64)
    used = np.zeros(n, dtype=np.bool_)
    tour[0] = 0
    used[0] = True
    for k in range(1, n):
        prev = tour[k - 1]
        best, bd = -1, np.inf
        for j in range(n):
            if not used[j] and D[prev, j] < bd:
                bd = D[prev, j]
                best = j
        tour[k] = best
        used[best] = True
    return tour


@numba.njit(fastmath=True)
def two_opt_delta(D, tour, i, j):
    n = tour.shape[0]
    a = tour[i - 1] if i > 0 else tour[n - 1]
    b = tour[i]
    c = tour[j]
    d = tour[j + 1] if j + 1 < n else tour[0]
    return (D[a, c] + D[b, d]) - (D[a, b] + D[c, d])


@numba.njit(fastmath=True)
def apply_two_opt(tour, i, j):
    while i < j:
        t = tour[i]
        tour[i] = tour[j]
        tour[j] = t
        i += 1
        j -= 1


@numba.njit(fastmath=True)
def two_opt_best(D, tour):
    n = tour.shape[0]
    improved = True
    while improved:
        improved = False
        bi, bj, bd = -1, -1, -1e-12
        for i in range(n):
            for j in range(i + 1, n):
                if j == i + 1 or (i == 0 and j == n - 1):
                    continue
                dl = two_opt_delta(D, tour, i, j)
                if dl < bd:
                    bd = dl
                    bi, bj = i, j
        if bi >= 0:
            apply_two_opt(tour, bi, bj)
            improved = True
    return tour


@numba.njit(fastmath=True)
def xorshift(state):
    state[0] ^= state[0] << np.int64(13)
    state[0] ^= state[0] >> np.int64(7)
    state[0] ^= state[0] << np.int64(17)
    return state[0]


@numba.njit(fastmath=True)
def sa_tsp(D, init, iters, t0, t1, seed):
    n = D.shape[0]
    tour = init.copy()
    cur = tour_len_matrix(D, tour)
    best = cur
    state = np.array([np.int64(seed if seed else 1)], dtype=np.int64)
    log_ratio = np.log(t1 / t0)
    for it in range(iters):
        u = xorshift(state)
        i = int(u % np.int64(n))
        if i < 0:
            i += n
        u = xorshift(state)
        j = int(u % np.int64(n))
        if j < 0:
            j += n
        if i == j:
            continue
        if i > j:
            t = i
            i = j
            j = t
        if j == i + 1 or (i == 0 and j == n - 1):
            continue
        dl = two_opt_delta(D, tour, i, j)
        frac = float(it) / float(iters)
        T = t0 * np.exp(log_ratio * frac)
        u = xorshift(state)
        r = float(u if u >= 0 else -u) / 9.223372036854776e18
        if dl < 0.0 or r < np.exp(-dl / T):
            apply_two_opt(tour, i, j)
            cur += dl
            if cur < best:
                best = cur
    return best, tour


@numba.njit(fastmath=True)
def held_karp_length(D):
    n = D.shape[0]
    if n == 1:
        return 0.0
    N = 1 << n
    INF = np.inf
    dp = np.empty((N, n), dtype=np.float64)
    for m in range(N):
        for i in range(n):
            dp[m, i] = INF
    dp[1, 0] = 0.0
    for mask in range(N):
        if not (mask & 1):
            continue
        for last in range(n):
            if not (mask >> last) & 1:
                continue
            cur = dp[mask, last]
            if cur == INF:
                continue
            for nxt in range(n):
                if (mask >> nxt) & 1:
                    continue
                nm = mask | (1 << nxt)
                nd = cur + D[last, nxt]
                if nd < dp[nm, nxt]:
                    dp[nm, nxt] = nd
    full = N - 1
    best = INF
    for last in range(1, n):
        cand = dp[full, last] + D[last, 0]
        if cand < best:
            best = cand
    return best


# ----------------------------------------------------------------------------
# Method drivers (each returns tour_length, runtime_ms)
# ----------------------------------------------------------------------------
def run_hrvst(coords, seed):
    t0 = time.perf_counter()
    cand = solver.build_candidates_small(coords)
    eu, ev = solver.build_edges(coords, cand)
    tour, _, _, L1, _, _ = solver.solve_staged(coords, cand, eu, ev, seed)
    t1 = time.perf_counter()
    return float(L1), (t1 - t0) * 1000.0


def run_nn(D):
    t0 = time.perf_counter()
    tour = nn_tour(D)
    L = float(tour_len_matrix(D, tour))
    return L, (time.perf_counter() - t0) * 1000.0


def run_two_opt(D):
    t0 = time.perf_counter()
    tour = nn_tour(D).copy()
    two_opt_best(D, tour)
    L = float(tour_len_matrix(D, tour))
    return L, (time.perf_counter() - t0) * 1000.0


def run_sa(D, seed):
    t0 = time.perf_counter()
    init = nn_tour(D).copy()
    n = D.shape[0]
    best, _ = sa_tsp(D, init, SA_ITERS_PER_N2 * n * n, SA_T0, SA_T1, seed + 7919)
    return float(best), (time.perf_counter() - t0) * 1000.0


def instance_coords(n, seed):
    rng = np.random.default_rng(seed)
    return np.ascontiguousarray(rng.random((n, 2)), dtype=np.float64)


def warmup():
    c = instance_coords(20, 0)
    D = np.sqrt(((c[:, None, :] - c[None, :, :]) ** 2).sum(-1))
    run_hrvst(c, 0)
    run_nn(D)
    run_two_opt(D)
    run_sa(D, 0)
    held_karp_length(D[:10])


# ----------------------------------------------------------------------------
# Benchmark harness
# ----------------------------------------------------------------------------
def main():
    print("warming up (numba compile, untimed) ...", flush=True)
    warmup()
    rows = []
    for n in SIZES:
        use_exact = n <= 15
        ref_kind = "held_karp" if use_exact else "best_known"
        for trial in range(TRIALS):
            seed = n * 1000 + trial
            coords = instance_coords(n, seed)
            diff = coords[:, None, :] - coords[None, :, :]
            D = np.sqrt((diff * diff).sum(-1))
            lens, times = {}, {}
            lens["hrvst"], times["hrvst"] = run_hrvst(coords, seed)
            lens["nearest_neighbor"], times["nearest_neighbor"] = run_nn(D)
            lens["two_opt"], times["two_opt"] = run_two_opt(D)
            lens["simulated_annealing"], times["simulated_annealing"] = run_sa(D, seed)
            if use_exact:
                ref = float(held_karp_length(D))
            else:
                ref = min(lens.values())
            for m in METHODS:
                gap = 100.0 * (lens[m] / ref - 1.0)
                rows.append({
                    "size_n": n, "trial": trial, "seed": seed,
                    "instance": "euclidean", "method": m,
                    "tour_length": lens[m], "reference_length": ref,
                    "reference_kind": ref_kind, "gap_pct": gap,
                    "runtime_ms": times[m],
                })
        print(f"  N={n}: done ({TRIALS} trials x {len(METHODS)} methods)", flush=True)

    csv_path = os.path.join(HERE, "results.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["size_n", "trial", "seed", "instance",
                                          "method", "tour_length", "reference_length",
                                          "reference_kind", "gap_pct", "runtime_ms"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {csv_path} ({len(rows)} rows)", flush=True)
    summary = summarize(rows)
    print_summary(summary)
    plot_fig1(summary)
    plot_fig2(summary)


def summarize(rows):
    from collections import defaultdict
    agg = defaultdict(list)
    for r in rows:
        agg[(r["size_n"], r["method"])].append(r)
    out = {}
    for key, rs in agg.items():
        gaps = [r["gap_pct"] for r in rs]
        ms = [r["runtime_ms"] for r in rs]
        out[key] = {
            "mean_gap": float(np.mean(gaps)),
            "worst_gap": float(np.max(gaps)),
            "mean_ms": float(np.mean(ms)),
        }
    return out


def print_summary(summary):
    print("\n==== summary: mean gap % / worst gap % / mean ms ====")
    print(f"{'N':>4} {'method':>20} {'mean_gap':>9} {'worst_gap':>10} {'mean_ms':>10}")
    for n in SIZES:
        for m in METHODS:
            s = summary[(n, m)]
            print(f"{n:>4} {m:>20} {s['mean_gap']:>8.3f}% {s['worst_gap']:>9.3f}% {s['mean_ms']:>10.2f}")
    print("======================================================\n")


def plot_fig1(summary):
    fig, ax = plt.subplots(figsize=(8, 5))
    for m in METHODS:
        xs = SIZES
        ys = [summary[(n, m)]["mean_gap"] for n in xs]
        worst = [summary[(n, m)]["worst_gap"] for n in xs]
        lo = [0.0] * len(xs)
        hi = [w - y for w, y in zip(worst, ys)]
        ax.errorbar(xs, ys, yerr=[lo, hi], label=LABELS[m], color=COLORS[m],
                    marker=MARKERS[m], capsize=4, linewidth=1.8)
    ax.set_xlabel("Problem size N")
    ax.set_ylabel("Optimality gap (%)")
    ax.set_title("Optimality gap vs problem size (mean, whisker = worst of 100 trials)")
    ax.set_xticks(SIZES)
    ax.legend(frameon=True)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    p = os.path.join(HERE, "fig1_optimality_gap.png")
    fig.savefig(p, dpi=300)
    plt.close(fig)
    print(f"wrote {p}", flush=True)


def plot_fig2(summary):
    fig, ax = plt.subplots(figsize=(7.5, 5))
    for m in METHODS:
        xs = [summary[(n, m)]["mean_ms"] for n in SIZES]
        ys = [summary[(n, m)]["mean_gap"] for n in SIZES]
        ax.plot(xs, ys, label=LABELS[m], color=COLORS[m],
                marker=MARKERS[m], linewidth=1.8)
        for x, y, n in zip(xs, ys, SIZES):
            ax.annotate(f"N={n}", (x, y), textcoords="offset points",
                        xytext=ANNOTATE_XY[m], fontsize=8)
    ax.set_xscale("log")
    ax.set_xlabel("Mean runtime per trial (ms, log scale)")
    ax.set_ylabel("Mean optimality gap (%)")
    ax.set_title("Pareto frontier: runtime vs optimality gap")
    ax.legend(frameon=True)
    ax.grid(True, alpha=0.3, which="both")
    fig.tight_layout()
    p = os.path.join(HERE, "fig2_pareto_frontier.png")
    fig.savefig(p, dpi=300)
    plt.close(fig)
    print(f"wrote {p}", flush=True)


if __name__ == "__main__":
    main()
