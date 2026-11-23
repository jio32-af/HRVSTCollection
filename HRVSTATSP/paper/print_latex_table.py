#!/usr/bin/env python3
"""Print the exact aggregated benchmark summary as a LaTeX tabular block.

Reads paper/results.csv (no rerun needed) and aggregates per (N, method):
mean gap %, worst gap %, win rate % (share of trials attaining the trial's
best tour length, ties count), and mean runtime ms. Paste stdout straight
into Overleaf (needs \\usepackage{booktabs}).
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
SIZES = [10, 15, 25, 50]
METHODS = ["hrvst", "nearest_neighbor", "two_opt", "simulated_annealing"]
NAMES = {
    "hrvst": "HRVST (ours)",
    "nearest_neighbor": "Nearest Neighbor",
    "two_opt": "Standard 2-Opt",
    "simulated_annealing": "Simulated Annealing",
}


def main():
    with open(os.path.join(HERE, "results.csv")) as f:
        rows = list(csv.DictReader(f))
    by_trial = defaultdict(list)
    for r in rows:
        by_trial[(int(r["size_n"]), int(r["trial"]))].append(r)
    best_of = {}
    for key, rs in by_trial.items():
        best_of[key] = min(float(r["tour_length"]) for r in rs)

    print("\\begin{tabular}{llrrrr}")
    print("\\toprule")
    print("Method & $N$ & Mean gap (\\%) & Worst gap (\\%) & Win rate (\\%) & Time (ms) \\\\")
    print("\\midrule")
    for m in METHODS:
        for n in SIZES:
            rs = [r for r in rows if int(r["size_n"]) == n and r["method"] == m]
            gaps = [float(r["gap_pct"]) for r in rs]
            mean_gap = sum(gaps) / len(gaps)
            worst_gap = max(gaps)
            wins = sum(
                1 for r in rs
                if abs(float(r["tour_length"]) - best_of[(n, int(r["trial"]))])
                <= 1e-9 * max(1.0, best_of[(n, int(r["trial"]))])
            )
            win_rate = 100.0 * wins / len(rs)
            mean_ms = sum(float(r["runtime_ms"]) for r in rs) / len(rs)
            print(f"{NAMES[m]} & {n} & {mean_gap:.3f} & {worst_gap:.3f} & "
                  f"{win_rate:.1f} & {mean_ms:.2f} \\\\")
    print("\\bottomrule")
    print("\\end{tabular}")


if __name__ == "__main__":
    main()
