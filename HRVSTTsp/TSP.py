#!/usr/bin/env python3
"""TSP — large-scale 2D Uniform Euclidean benchmark suite.

Random points in the 1x1 unit square, solved by a fully Numba-compiled
(@njit, fastmath=True) pipeline with NO Held-Karp solver, NO O(n^2)
distance matrix, and NO pure-Python loops anywhere in the solver path:

  coordinates (contiguous float64) -> Delaunay+MST-first 48-nearest
  candidate lists (all Delaunay + Kruskal MST neighbors kept,
  25-iteration subgradient-alpha-ranked KNN fill and row order)
  -> multi-fragment greedy edge insertion, shortest candidate edges
  first (on-the-fly Euclidean math, no jumps) -> single exact-edge
  construction, fast-polished (2-Opt, one 3-Opt rotation) -> final
  deep polish: 2-Opt / 3-Opt convergence cascade + lean ILS
  (geometrically clustered double-bridge kicks, DLB-targeted 2/3-Opt
  recovery, keep-best with 60-kick patience). Ablation-pruned:
  multi-start, IPT merging, 4-Opt, Lin-Kernighan, and Or-opt measured
  no material beta contribution and were deleted.

A tiny warmup call (n = 20) compiles all native code before timing, then
the benchmark runs n = 100, 1000, 10000, 100000, 1000000 and logs
execution time, total tour length, the initial beta right after tour
construction, and the final beta after local search — both as
tour_length / sqrt(n) against the ~0.7124 theoretical optimum.

Needs: numpy, scipy, numba.
    pip install numpy scipy numba

Run:
    python3 TSP.py                      (asks cases + nodes, edge test)
    python3 TSP.py --sizes 100 1000     (scale series over given sizes)
    python3 TSP.py selftest             (correctness + determinism checks)
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import time

import numpy as np
from scipy.spatial import Delaunay, QhullError, cKDTree
import numba

K = 48            # final candidate list size per city (Delaunay-filtered)
KC3 = 24          # nearest candidates per city used at 3-Opt's k level
BHH = 0.7124      # Beardwood-Halton-Hammersley constant (unit square)
SIZES = [100, 1000, 10000, 100000, 1000000]
TOL = 1e-12       # improvement tolerance for move acceptance
ILS_STALE = 60    # kick patience: stop after this many non-improving kicks


@numba.njit(fastmath=True)
def edist(x1, y1, x2, y2):
    dx = x1 - x2
    dy = y1 - y2
    return math.sqrt(dx * dx + dy * dy)


@numba.njit(fastmath=True)
def tour_len(coords, tour, n):
    s = 0.0
    for i in range(n - 1):
        a = tour[i]
        b = tour[i + 1]
        s += edist(coords[a, 0], coords[a, 1], coords[b, 0], coords[b, 1])
    a = tour[n - 1]
    b = tour[0]
    s += edist(coords[a, 0], coords[a, 1], coords[b, 0], coords[b, 1])
    return s


@numba.njit(fastmath=True)
def dsu_find(parent, x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


@numba.njit(fastmath=True)
def link_edge(deg, ea, eb, u, v):
    """Record undirected tour edge (u, v); callers guarantee deg < 2."""
    if deg[u] == 0:
        ea[u] = v
    else:
        eb[u] = v
    if deg[v] == 0:
        ea[v] = u
    else:
        eb[v] = u
    deg[u] += 1
    deg[v] += 1


@numba.njit(fastmath=True)
def mf_union(eu, ev, E, parent, psize, deg, ea, eb, n):
    """Multi-fragment greedy edge insertion over length-sorted edges.

    Accepts an edge only if both ends have degree < 2 and it does not
    close a premature cycle (same-fragment links allowed only once a
    single spanning fragment remains). Every accepted edge comes from
    the KD-tree candidate lists, so no stranded city ever forces a
    cross-map jump. Returns the remaining fragment count.
    """
    comp = n
    for v in range(n):
        parent[v] = v
        psize[v] = 1
        deg[v] = 0
        ea[v] = -1
        eb[v] = -1
    added = 0
    for e in range(E):
        u = eu[e]
        v = ev[e]
        if u == v or deg[u] >= 2 or deg[v] >= 2:
            continue
        ru = dsu_find(parent, u)
        rv = dsu_find(parent, v)
        if ru == rv and comp != 1:
            continue
        link_edge(deg, ea, eb, u, v)
        added += 1
        if ru != rv:
            if psize[ru] < psize[rv]:
                tmp = ru
                ru = rv
                rv = tmp
            parent[rv] = ru
            psize[ru] += psize[rv]
            comp -= 1
        if added == n:
            break
    return comp


@numba.njit(fastmath=True)
def mf_patch_candidates(coords, cand, K, parent, psize, deg, ea, eb, comp, n):
    """Join fragment endpoints through mutual candidate edges (zero-jump
    patching): up to 5 sweeps linking degree-1 cities of different
    fragments. Strict cross-fragment rule, so nothing seals early."""
    for _ in range(5):
        moved = 0
        for u in range(n):
            if deg[u] != 1:
                continue
            for t in range(K):
                v = cand[u, t]
                if v == u or deg[v] != 1:
                    continue
                ru = dsu_find(parent, u)
                rv = dsu_find(parent, v)
                if ru == rv:
                    continue
                link_edge(deg, ea, eb, u, v)
                if psize[ru] < psize[rv]:
                    tmp = ru
                    ru = rv
                    rv = tmp
                parent[rv] = ru
                psize[ru] += psize[rv]
                comp -= 1
                moved += 1
                break
        if moved == 0:
            break
    return comp


@numba.njit(fastmath=True)
def mf_patch_close(coords, parent, psize, deg, ea, eb, work, comp, n):
    """Provable-termination patching: repeatedly pair each deficient city
    with its nearest valid partner (degree < 2, different fragment unless
    a single spanning component remains). Every added edge drops total
    stubs by exactly 2 while the endpoint stack shrinks, so the loop must
    terminate; the cycle guard means no fragment ever seals prematurely.
    Returns the remaining fragment count (1 on success)."""
    top = 0
    for v in range(n):
        if deg[v] < 2:
            work[top] = v
            top += 1
    guard = top * 4 + 100
    while top > 0 and guard > 0:
        guard -= 1
        top -= 1
        u = work[top]
        if deg[u] >= 2:
            continue
        best = -1
        bestd = np.inf
        for s in range(top):
            v = work[s]
            if v == u or deg[v] >= 2:
                continue
            if dsu_find(parent, u) == dsu_find(parent, v) and comp != 1:
                continue
            d = edist(coords[u, 0], coords[u, 1], coords[v, 0], coords[v, 1])
            if d < bestd:
                bestd = d
                best = v
        if best == -1:
            # No valid partner on the stack: every other deficient city
            # shares u's fragment, so it already spans all n and any live
            # mate closes the tour. Requeue (bounded by guard) and retry.
            work[top] = u
            top += 1
            continue
        link_edge(deg, ea, eb, u, best)
        ru = dsu_find(parent, u)
        rv = dsu_find(parent, best)
        if ru != rv:
            if psize[ru] < psize[rv]:
                tmp = ru
                ru = rv
                rv = tmp
            parent[rv] = ru
            psize[ru] += psize[rv]
            comp -= 1
        if deg[u] < 2:
            work[top] = u
            top += 1
        if deg[best] < 2:
            work[top] = best
            top += 1
    return comp


@numba.njit(fastmath=True)
def mf_assemble(ea, eb, tour, pos, n):
    """Walk the degree-2 adjacency from city 0 into tour order."""
    prev = -1
    cur = 0
    for p in range(n):
        tour[p] = cur
        pos[cur] = p
        a = ea[cur]
        b = eb[cur]
        if a != prev:
            nxt = a
        else:
            nxt = b
        prev = cur
        cur = nxt


@numba.njit(fastmath=True)
def construct_initial(coords, cand, K, eu, ev, E, parent, psize, deg, ea, eb,
                      work, tour, pos, n):
    """Multi-fragment greedy construction: sorted union, candidate
    patching, provable-closure patching, then assembly. n == 1 yields
    the trivial single-city tour."""
    if n == 1:
        tour[0] = 0
        pos[0] = 0
        deg[0] = 2
        return
    comp = mf_union(eu, ev, E, parent, psize, deg, ea, eb, n)
    comp = mf_patch_candidates(coords, cand, K, parent, psize, deg, ea, eb,
                               comp, n)
    comp = mf_patch_close(coords, parent, psize, deg, ea, eb, work, comp, n)
    mf_assemble(ea, eb, tour, pos, n)


@numba.njit(fastmath=True)
def two_opt_sweep(coords, tour, pos, cand, K, active, n):
    """One 2-Opt sweep in tour order with don't-look bits.

    Only pairs (i, j) where j is the position of a candidate city of
    tour[i] are tested, with O(1) delta evaluation. Returns the number
    of improving moves applied.
    """
    imp = 0
    for i in range(n):
        a = tour[i]
        if active[a] == 0:
            continue
        if i + 1 < n:
            b = tour[i + 1]
        else:
            b = tour[0]
        dab = edist(coords[a, 0], coords[a, 1], coords[b, 0], coords[b, 1])
        improved = False
        for t in range(K):
            v = cand[a, t]
            j = pos[v]
            if j <= i + 1:
                continue
            if i == 0 and j == n - 1:
                continue
            c = tour[j]
            if j + 1 < n:
                d = tour[j + 1]
            else:
                d = tour[0]
            delta = (edist(coords[a, 0], coords[a, 1], coords[c, 0], coords[c, 1])
                     + edist(coords[b, 0], coords[b, 1], coords[d, 0], coords[d, 1])
                     - dab
                     - edist(coords[c, 0], coords[c, 1], coords[d, 0], coords[d, 1]))
            if delta < -TOL:
                aa = i + 1
                bb = j
                while aa < bb:
                    u = tour[aa]
                    w = tour[bb]
                    tour[aa] = w
                    tour[bb] = u
                    pos[u] = bb
                    pos[w] = aa
                    aa += 1
                    bb -= 1
                for u in (a, b, c, d):
                    active[u] = 1
                    for t2 in range(K):
                        active[cand[u, t2]] = 1
                imp += 1
                improved = True
                break
        if not improved:
            active[a] = 0
    return imp


@numba.njit(fastmath=True)
def two_opt(coords, tour, pos, cand, K, active, n, max_sweeps):
    tot = 0
    for _ in range(max_sweeps):
        m = two_opt_sweep(coords, tour, pos, cand, K, active, n)
        tot += m
        if m == 0:
            break
    return tot


@numba.njit(fastmath=True)
def rotate_tour(tour, pos, buf, r, n):
    """Left-rotate the tour representation by r (O(n); DLB-safe)."""
    for p in range(n):
        buf[p] = tour[(p + r) % n]
    for p in range(n):
        tour[p] = buf[p]
        pos[buf[p]] = p


@numba.njit(fastmath=True)
def copy_seg(buf, dp, tour, l, r, rev):
    """Copy linear slice tour[l..r] to buf[dp..], reversed if rev."""
    if rev == 0:
        p = l
        while p <= r:
            buf[dp] = tour[p]
            dp += 1
            p += 1
    else:
        p = r
        while p >= l:
            buf[dp] = tour[p]
            dp += 1
            p -= 1
    return dp


@numba.njit(fastmath=True)
def db_rebuild(tour, pos, buf, i, j, k, l, n):
    """Apply double-bridge X+Z+Y+W for linear cuts i<j<k<l (O(n))."""
    dp = 0
    dp = copy_seg(buf, dp, tour, i + 1, j, 0)
    dp = copy_seg(buf, dp, tour, k + 1, l, 0)
    dp = copy_seg(buf, dp, tour, j + 1, k, 0)
    dp = copy_seg(buf, dp, tour, l + 1, n - 1, 0)
    dp = copy_seg(buf, dp, tour, 0, i, 0)
    for p in range(n):
        tour[p] = buf[p]
        pos[buf[p]] = p


@numba.njit(fastmath=True)
def three_opt_sweep(coords, tour, pos, cand, K, active, buf, n):
    """One targeted 3-Opt sweep over linear cuts i<j<k.

    Cut j ranges over positions of candidates of tour[i]/tour[i+1] and
    cut k over positions of the nearest KC3 candidates of tour[j]/
    tour[j+1] (second-level restriction keeps the sweep O(n*K*KC3)).
    All 8 arc reconnections are scored with O(1) deltas; winners are
    applied via a buffer rebuild. Returns improving moves applied.
    """
    imp = 0
    for i in range(n - 3):
        a0 = tour[i]
        a1 = tour[i + 1]
        if active[a0] == 0 and active[a1] == 0:
            continue
        improved = False
        for t1 in range(2 * K):
            a0 = tour[i]
            a1 = tour[i + 1]
            if t1 < K:
                u = cand[a0, t1]
            else:
                u = cand[a1, t1 - K]
            j = pos[u]
            if j < i + 2 or j > n - 3:
                continue
            for t2 in range(2 * KC3):
                bj0 = tour[j]
                bj1 = tour[j + 1]
                if t2 < KC3:
                    w = cand[bj0, t2]
                else:
                    w = cand[bj1, t2 - KC3]
                k = pos[w]
                if k < j + 1 or k > n - 2:
                    continue
                x0 = tour[i + 1]
                x1 = tour[j]
                y0 = tour[j + 1]
                y1 = tour[k]
                z0 = tour[k + 1]
                ai = tour[i]
                z1 = ai
                removed = (edist(coords[ai, 0], coords[ai, 1], coords[x0, 0], coords[x0, 1])
                           + edist(coords[x1, 0], coords[x1, 1], coords[y0, 0], coords[y0, 1])
                           + edist(coords[y1, 0], coords[y1, 1], coords[z0, 0], coords[z0, 1]))
                which = -1
                if (edist(coords[x1, 0], coords[x1, 1], coords[z0, 0], coords[z0, 1])
                        + edist(coords[z1, 0], coords[z1, 1], coords[y0, 0], coords[y0, 1])
                        + edist(coords[y1, 0], coords[y1, 1], coords[x0, 0], coords[x0, 1])
                        - removed < -TOL):
                    which = 0
                elif (edist(coords[x1, 0], coords[x1, 1], coords[z1, 0], coords[z1, 1])
                        + edist(coords[z0, 0], coords[z0, 1], coords[y0, 0], coords[y0, 1])
                        + edist(coords[y1, 0], coords[y1, 1], coords[x0, 0], coords[x0, 1])
                        - removed < -TOL):
                    which = 1
                elif (edist(coords[x1, 0], coords[x1, 1], coords[z0, 0], coords[z0, 1])
                        + edist(coords[z1, 0], coords[z1, 1], coords[y1, 0], coords[y1, 1])
                        + edist(coords[y0, 0], coords[y0, 1], coords[x0, 0], coords[x0, 1])
                        - removed < -TOL):
                    which = 2
                elif (edist(coords[x1, 0], coords[x1, 1], coords[z1, 0], coords[z1, 1])
                        + edist(coords[z0, 0], coords[z0, 1], coords[y1, 0], coords[y1, 1])
                        + edist(coords[y0, 0], coords[y0, 1], coords[x0, 0], coords[x0, 1])
                        - removed < -TOL):
                    which = 3
                elif (edist(coords[y1, 0], coords[y1, 1], coords[x0, 0], coords[x0, 1])
                        + edist(coords[x1, 0], coords[x1, 1], coords[z0, 0], coords[z0, 1])
                        + edist(coords[z1, 0], coords[z1, 1], coords[y0, 0], coords[y0, 1])
                        - removed < -TOL):
                    which = 4
                elif (edist(coords[y0, 0], coords[y0, 1], coords[x0, 0], coords[x0, 1])
                        + edist(coords[x1, 0], coords[x1, 1], coords[z0, 0], coords[z0, 1])
                        + edist(coords[z1, 0], coords[z1, 1], coords[y1, 0], coords[y1, 1])
                        - removed < -TOL):
                    which = 5
                elif (edist(coords[y1, 0], coords[y1, 1], coords[x1, 0], coords[x1, 1])
                        + edist(coords[x0, 0], coords[x0, 1], coords[z0, 0], coords[z0, 1])
                        + edist(coords[z1, 0], coords[z1, 1], coords[y0, 0], coords[y0, 1])
                        - removed < -TOL):
                    which = 6
                elif (edist(coords[y0, 0], coords[y0, 1], coords[x1, 0], coords[x1, 1])
                        + edist(coords[x0, 0], coords[x0, 1], coords[z0, 0], coords[z0, 1])
                        + edist(coords[z1, 0], coords[z1, 1], coords[y1, 0], coords[y1, 1])
                        - removed < -TOL):
                    which = 7
                if which >= 0:
                    dp = 0
                    if which <= 3:
                        dp = copy_seg(buf, dp, tour, i + 1, j, 0)
                        if which == 1 or which == 3:
                            dp = copy_seg(buf, dp, tour, 0, i, 1)
                            dp = copy_seg(buf, dp, tour, k + 1, n - 1, 1)
                        else:
                            dp = copy_seg(buf, dp, tour, k + 1, n - 1, 0)
                            dp = copy_seg(buf, dp, tour, 0, i, 0)
                        if which == 2 or which == 3:
                            dp = copy_seg(buf, dp, tour, j + 1, k, 1)
                        else:
                            dp = copy_seg(buf, dp, tour, j + 1, k, 0)
                    else:
                        if which == 5 or which == 7:
                            dp = copy_seg(buf, dp, tour, j + 1, k, 1)
                        else:
                            dp = copy_seg(buf, dp, tour, j + 1, k, 0)
                        if which == 6 or which == 7:
                            dp = copy_seg(buf, dp, tour, i + 1, j, 1)
                        else:
                            dp = copy_seg(buf, dp, tour, i + 1, j, 0)
                        dp = copy_seg(buf, dp, tour, k + 1, n - 1, 0)
                        dp = copy_seg(buf, dp, tour, 0, i, 0)
                    for p in range(n):
                        tour[p] = buf[p]
                        pos[buf[p]] = p
                    for u in (ai, x0, x1, y0, y1, z0, z1):
                        active[u] = 1
                        for t3 in range(K):
                            active[cand[u, t3]] = 1
                    imp += 1
                    improved = True
                    break
        if not improved:
            # tour[i..i+1] untouched in this iteration: retire the edge
            active[tour[i]] = 0
            active[tour[i + 1]] = 0
    return imp


@numba.njit(fastmath=True)
def three_opt_stage(coords, tour, pos, cand, K, active, buf, n, max_rot):
    """Targeted 3-Opt with full global coverage.

    Outer loop over rotations (each makes a different edge the wrap
    edge, so every triple is linear in some frame); inner loop runs
    don't-look-bit sweeps to convergence within each frame. Resetting
    the flags per rotation is required: retiring an edge in one frame
    must not suppress its examination under a later frame's triples.
    """
    r = 1 % n
    tot = 0
    for s in range(max_rot):
        rotate_tour(tour, pos, buf, r, n)
        for v in range(n):
            active[v] = 1
        for _ in range(6):
            m = three_opt_sweep(coords, tour, pos, cand, K, active, buf, n)
            tot += m
            if m == 0:
                break
        r = (r + n // 3 + 1) % n
        if r == 0:
            r = 1
    return tot


@numba.njit(fastmath=True)
def invert_pos(tour, pos, n):
    """Rebuild pos[city] = position from tour order."""
    for p in range(n):
        pos[tour[p]] = p


def light_polish(coords, tour, pos, cand, active, buf, n):
    """Fast single-start polish: 2-Opt, one 3-Opt rotation."""
    active[:] = 1
    m2 = two_opt(coords, tour, pos, cand, K, active, n, 10)
    m3 = three_opt_stage(coords, tour, pos, cand, K, active, buf, n, 1)
    return m2, m3


def deep_polish(coords, tour, pos, cand, active, buf, n, seed):
    """Final deep polish: full 2/3-Opt convergence cascade, lean ILS
    double-bridge kicks, then normalize start to city 0."""
    invert_pos(tour, pos, n)
    active[:] = 1
    m2 = two_opt(coords, tour, pos, cand, K, active, n, 30)
    m3 = three_opt_stage(coords, tour, pos, cand, K, active, buf, n, 3)
    m2 += two_opt(coords, tour, pos, cand, K, active, n, 30)
    m2 += two_opt(coords, tour, pos, cand, K, active, n, 10)
    acc = ils_stage(coords, tour, pos, cand, K, active, buf, n, seed)
    rotate_tour(tour, pos, buf, pos[0], n)
    return m2, m3, acc


@numba.njit
def _rng_next(state):
    """xorshift64 step on a 1-element int64 array (deterministic)."""
    x = state[0]
    x = x ^ (x << 13)
    x = x ^ (x >> 7)
    x = x ^ (x << 17)
    state[0] = x
    return x


@numba.njit
def _rng_range(state, lo, hi):
    """Deterministic integer in [lo, hi); hi must exceed lo."""
    span = hi - lo
    v = _rng_next(state) % span
    if v < 0:
        v += span
    return lo + v


@numba.njit(fastmath=True)
def ils_stage(coords, tour, pos, cand, K, active, buf, n, seed):
    """Lean ILS: double-bridge kicks over the 2/3-Opt core.

    Each kick cuts 4 tour edges - one uniform anchor city plus three
    cuts at tour positions of its candidate neighbors (near in space,
    possibly far on the tour), sorted into linear cuts - and
    reconnects double-bridge. Recovery runs 2-Opt + one 3-Opt
    sweep to convergence over the kick-activated neighborhoods only
    (junction cities + candidate neighbors). Keep-only-improvements
    with ILS_STALE-kick patience. Deterministic via xorshift64.
    """
    st = np.zeros(1, dtype=np.int64)
    st[0] = seed
    best = np.empty(n, dtype=np.int64)
    best_len = tour_len(coords, tour, n)
    for p in range(n):
        best[p] = tour[p]
    stale = 0
    acc = 0
    while stale < ILS_STALE:
        if n > 8:
            # Geometrically clustered kick: one uniform anchor city plus
            # three cuts at tour positions of its candidate neighbors
            # (near in space, possibly far on the tour), sorted into
            # linear cuts twisting one spatial neighborhood.
            a = _rng_range(st, 0, n)
            q0 = pos[a]
            q1 = pos[cand[a, _rng_range(st, 0, K)]]
            q2 = pos[cand[a, _rng_range(st, 0, K)]]
            q3 = pos[cand[a, _rng_range(st, 0, K)]]
            if q1 < q0:
                t = q0
                q0 = q1
                q1 = t
            if q3 < q2:
                t = q2
                q2 = q3
                q3 = t
            if q2 < q0:
                t = q0
                q0 = q2
                q2 = t
            if q3 < q1:
                t = q1
                q1 = q3
                q3 = t
            if q2 < q1:
                t = q1
                q1 = q2
                q2 = t
            i = q0
            j = q1
            k = q2
            l = q3
            db_rebuild(tour, pos, buf, i, j, k, l, n)
            lx = j - i
            lz = l - k
            ly = k - j
            base = lx + lz
            tip = base + ly
            for pp in (lx - 1, lx, base - 1, base, tip - 1, tip, 0, n - 1):
                c = tour[pp]
                active[c] = 1
                for t in range(K):
                    active[cand[c, t]] = 1
        for _ in range(5):
            c0 = two_opt(coords, tour, pos, cand, K, active, n, 10)
            c1 = three_opt_sweep(coords, tour, pos, cand, K, active, buf,
                                 n)
            if c0 + c1 == 0:
                break
        L = tour_len(coords, tour, n)
        if L < best_len - TOL:
            for p in range(n):
                best[p] = tour[p]
            best_len = L
            stale = 0
            acc += 1
        else:
            stale += 1
            for p in range(n):
                tour[p] = best[p]
            invert_pos(tour, pos, n)
    return acc


def build_candidates_small(coords):
    """KNN-only candidates with self-padding (tiny-n path, n <= 64)."""
    n = coords.shape[0]
    tree = cKDTree(coords)
    kq = K + 1 if n > K else n
    res = tree.query(coords, k=kq, workers=-1)
    idx = res[1]
    del res
    if kq == 1:
        neigh = np.empty((n, 0), dtype=np.int64)
    else:
        neigh = idx[:, 1:].astype(np.int64)
    m = neigh.shape[1]
    cand = np.empty((n, K), dtype=np.int64)
    if m > 0:
        cand[:, :m] = neigh
    if m < K:
        cand[:, m:] = np.arange(n, dtype=np.int64)[:, None]
    return np.ascontiguousarray(cand)


@numba.njit(fastmath=True)
def mst_full(pu, pv, pd, P, parent, psize, tu, tv, td, n, need):
    """Kruskal spanning tree over a length-sorted pool (see alpha stage).

    Accepts pool edges in order until need are taken, recording each
    length into td. Returns edges taken (below need only when the pool
    is disconnected — callers fall back to distance ranking then).
    """
    for v in range(n):
        parent[v] = v
        psize[v] = 1
    m = 0
    for e in range(P):
        u = pu[e]
        v = pv[e]
        if u == v:
            continue
        ru = dsu_find(parent, u)
        rv = dsu_find(parent, v)
        if ru == rv:
            continue
        tu[m] = u
        tv[m] = v
        td[m] = pd[e]
        m += 1
        if psize[ru] < psize[rv]:
            tmp = ru
            ru = rv
            rv = tmp
        parent[rv] = ru
        psize[ru] += psize[rv]
        if m == need:
            break
    return m


@numba.njit(fastmath=True)
def lca_build(tu, tv, td, m, LOG, up, mx, depth, stack, n):
    """Binary-lifting LCA over a spanning tree (CSR built inline).

    up[k, v] / mx[k, v] are the 2^k-th ancestor of v and the max edge
    weight lifted over, depth roots at city 0. All loops are compiled;
    no Python-level per-edge work.
    """
    deg = np.zeros(n, dtype=np.int64)
    for e in range(m):
        deg[tu[e]] += 1
        deg[tv[e]] += 1
    start = np.empty(n + 1, dtype=np.int64)
    s = 0
    for v in range(n):
        start[v] = s
        s += deg[v]
    start[n] = s
    nbr = np.empty(2 * m, dtype=np.int64)
    wgt = np.empty(2 * m, dtype=np.float32)
    cur = np.empty(n, dtype=np.int64)
    for v in range(n):
        cur[v] = start[v]
    for e in range(m):
        u = tu[e]
        v = tv[e]
        w = td[e]
        nbr[cur[u]] = v
        wgt[cur[u]] = w
        cur[u] += 1
        nbr[cur[v]] = u
        wgt[cur[v]] = w
        cur[v] += 1
    for v in range(n):
        depth[v] = -1
    depth[0] = 0
    up[0, 0] = 0
    mx[0, 0] = 0.0
    top = 1
    stack[0] = 0
    while top > 0:
        top -= 1
        c = stack[top]
        for p in range(start[c], start[c + 1]):
            nb = nbr[p]
            if depth[nb] != -1:
                continue
            depth[nb] = depth[c] + 1
            up[0, nb] = c
            mx[0, nb] = wgt[p]
            stack[top] = nb
            top += 1
    for k in range(1, LOG):
        for v in range(n):
            a = up[k - 1, v]
            up[k, v] = up[k - 1, a]
            b = mx[k - 1, v]
            c2 = mx[k - 1, a]
            if b > c2:
                mx[k, v] = b
            else:
                mx[k, v] = c2


@numba.njit(fastmath=True)
def alpha_batch(up, mx, depth, LOG, qu, qv, qd, out, Q):
    """Alpha per query pair: d(u,v) minus the max tree-edge weight on
    the u-v path (0 for tree edges, whose path is the edge itself).
    Negative scores mark edges the tree would rather use."""
    for i in range(Q):
        u = qu[i]
        v = qv[i]
        best = 0.0
        if depth[u] < depth[v]:
            tmp = u
            u = v
            v = tmp
        d = depth[u] - depth[v]
        k = 0
        while d > 0:
            if d & 1:
                if mx[k, u] > best:
                    best = mx[k, u]
                u = up[k, u]
            d >>= 1
            k += 1
        if u != v:
            for k in range(LOG - 1, -1, -1):
                if up[k, u] != up[k, v]:
                    if mx[k, u] > best:
                        best = mx[k, u]
                    if mx[k, v] > best:
                        best = mx[k, v]
                    u = up[k, u]
                    v = up[k, v]
            if mx[0, u] > best:
                best = mx[0, u]
            if mx[0, v] > best:
                best = mx[0, v]
        out[i] = qd[i] - best


@numba.njit(fastmath=True)
def kruskal_mst(eu, ev, E, parent, psize, mst, mcnt, MM, n):
    """Kruskal forest over length-sorted edges (see mst_neighbors).

    Records an edge only when it joins two components AND both ends
    still hold fewer than MM recorded edges; skipped edges never union,
    so later short edges stay eligible. Per-city arrival order is length
    order, so each city's kept slots are its shortest MST edges. No
    connectivity is required: forest edges serve as candidates only.
    """
    for v in range(n):
        parent[v] = v
        psize[v] = 1
    for e in range(E):
        u = eu[e]
        v = ev[e]
        if u == v:
            continue
        ru = dsu_find(parent, u)
        rv = dsu_find(parent, v)
        if ru == rv:
            continue
        if mcnt[u] >= MM or mcnt[v] >= MM:
            continue
        mst[u, mcnt[u]] = v
        mcnt[u] += 1
        mst[v, mcnt[v]] = u
        mcnt[v] += 1
        if psize[ru] < psize[rv]:
            tmp = ru
            ru = rv
            rv = tmp
        parent[rv] = ru
        psize[ru] += psize[rv]


def mst_neighbors(coords):
    """Kruskal neighbor sets (<=8 shortest MST edges per city) over a
    16-NN edge pool, as (ids, lengths) with -1/inf padding.

    The pool MST approximates the Euclidean MST on uniform instances;
    its edges carry global wirings that pure KNN sets miss, which is
    exactly the structural information k-opt stalls for lack of.
    """
    n = coords.shape[0]
    MM = 8
    tree = cKDTree(coords)
    knn = np.ascontiguousarray(tree.query(coords, k=17, workers=-1)[1][:, 1:])
    del tree
    eu, ev = build_edges(coords, knn)
    del knn
    parent = np.empty(n, dtype=np.int64)
    psize = np.empty(n, dtype=np.int64)
    mcnt = np.zeros(n, dtype=np.int64)
    mst = np.full((n, MM), -1, dtype=np.int64)
    kruskal_mst(eu, ev, eu.shape[0], parent, psize, mst, mcnt, MM, n)
    del eu, ev, parent, psize, mcnt
    hit = mst != -1
    mc = np.maximum(mst, 0)
    dx = coords[np.arange(n)[:, None], 0] - coords[mc, 0]
    dy = coords[np.arange(n)[:, None], 1] - coords[mc, 1]
    del mc
    md = np.where(hit, np.sqrt(dx * dx + dy * dy), np.inf).astype(np.float32)
    del dx, dy, hit
    return mst, md


def build_candidates_big(coords, mw, md):
    """Delaunay+MST-first candidates: every Delaunay neighbor plus
    Kruskal MST neighbors kept unconditionally
    (deduplicated, Delaunay wins ties), remaining slots filled with
    the lowest-alpha KNN neighbors up to K = 48 per city, rows ranked
    by subgradient alpha (distance fallback when the 1-tree is
    disconnected).

    All ragged steps are vectorized (lexsort ranks, fancy assignment,
    one argpartition); no Python loop runs over n. The kd-tree query
    stays chunked past 200k rows to bound its transient. Falls back to
    KNN-only candidates with a printed warning if Qhull fails.
    """
    n = coords.shape[0]
    KD = 8             # Delaunay neighbors unconditionally kept per city
    KP = K + 2 * KD    # KNN pool width (covers overlap with Delaunay set)
    CS = 200000
    tree = cKDTree(coords)
    if n <= CS:
        res = tree.query(coords, k=KP + 1, workers=-1)
        Iknn = np.ascontiguousarray(res[1][:, 1:])
        Dknn = res[0][:, 1:].astype(np.float32)
        del res
    else:
        Il = []
        Dl = []
        for a in range(0, n, CS):
            r = tree.query(coords[a:a + CS], k=KP + 1, workers=-1)
            Il.append(np.ascontiguousarray(r[1][:, 1:]))
            Dl.append(r[0][:, 1:].astype(np.float32))
            del r
        Iknn = np.ascontiguousarray(np.vstack(Il))
        Dknn = np.ascontiguousarray(np.vstack(Dl))
        del Il, Dl
    try:
        tri = Delaunay(coords)
        indptr, indices = tri.vertex_neighbor_vertices
        del tri
        counts = np.diff(indptr).astype(np.int64)
        del indptr
        KD = int(counts.max())  # keep every Delaunay neighbor per city
        Du = np.repeat(np.arange(n, dtype=np.int32), counts)
        Dw = indices.astype(np.int32)
        del indices, counts
        Dd = np.sqrt((coords[Du, 0] - coords[Dw, 0]) ** 2
                     + (coords[Du, 1] - coords[Dw, 1]) ** 2).astype(np.float32)
        Pu, Pv, Pd = Du, Dw, Dd
        o = np.lexsort((Dd, Du))
        Uso, Wso, Dso = Du[o], Dw[o], Dd[o]
        del Du, Dw, Dd, o
        cnt = np.bincount(Uso, minlength=n)
        rank = np.arange(Uso.size) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        del cnt
        keep = rank < KD
        Uk, Wk, Dk, rk = Uso[keep], Wso[keep], Dso[keep], rank[keep]
        del Uso, Wso, Dso, rank, keep
        Ddel = np.full((n, KD), np.inf, dtype=np.float32)
        Wdel = np.full((n, KD), -1, dtype=np.int64)
        Ddel[Uk, rk] = Dk
        Wdel[Uk, rk] = Wk
        del Uk, Wk, Dk, rk
    except QhullError as exc:
        print(f"  (Delaunay failed ({exc}); using KNN-only candidates)")
        Ddel = np.full((n, KD), np.inf, dtype=np.float32)
        Wdel = np.full((n, KD), -1, dtype=np.int64)
        Pu = np.empty(0, dtype=np.int32)
        Pv = np.empty(0, dtype=np.int32)
        Pd = np.empty(0, dtype=np.float32)
    # Alpha stage: MST over the KNN+Delaunay pool, then LCA max-edge
    # alphas ranking every directed candidate pair (tree edges score 0).
    # Alpha order steers the fill and the KC prefixes at structural
    # edges instead of merely near ones. A disconnected pool falls back
    # to distance ranking.
    # Subgradient Held-Karp alpha stage: 25 iterations of minimum 1-tree
    # optimization under pi-modified weights on a 10-NN + Delaunay pool
    # (node 0 excluded from the MST part, rejoined by its 2 shortest
    # spokes each round). The final tree shapes candidate ranking only;
    # the pi schedule is heuristic, never reported as a bound.
    KP10 = 10
    rows = np.repeat(np.arange(n), KP10)
    cols = Iknn[:, :KP10].ravel()
    pu = np.concatenate([rows, Pu])
    pv = np.concatenate([cols, Pv])
    pd = np.concatenate([Dknn[:, :KP10].ravel(), Pd])
    del Pu, Pv
    keep = pu < pv
    pu, pv, pd = pu[keep], pv[keep], pd[keep]
    del keep
    zmask = pu == 0
    t0 = pv[zmask]
    d0 = pd[zmask]
    su = pu[~zmask]
    sv = pv[~zmask]
    sd = pd[~zmask]
    del pu, pv, pd, zmask
    pi = np.zeros(n, dtype=np.float64)
    emean = float(sd.mean())
    parent = np.empty(n, dtype=np.int64)
    psize = np.empty(n, dtype=np.int64)
    tu = np.empty(n, dtype=np.int64)
    tv = np.empty(n, dtype=np.int64)
    td = np.empty(n, dtype=np.float32)
    P = su.shape[0]
    for it in range(25):
        wp = sd + pi[su] + pi[sv]
        order = np.argsort(wp)
        del wp
        m = mst_full(su[order], sv[order], sd[order], P, parent, psize,
                     tu, tv, td, n, n - 2)
        del order
        if m < n - 2:
            break
        w0 = d0 + pi[t0]
        sp = np.argpartition(w0, 2)[:2]
        sa = int(sp[0])
        sb = int(sp[1])
        deg = np.bincount(np.concatenate([tu[:m], tv[:m],
                                          np.array([0, t0[sa], 0, t0[sb]])]),
                          minlength=n)
        pi += (0.25 * emean / (1.0 + 0.25 * it)) * (deg - 2)
    del su, sv, sd, parent, psize
    if m == n - 2:
        ks = sa if d0[sa] <= d0[sb] else sb
        tu2 = np.concatenate([tu[:m], np.array([0])])
        tv2 = np.concatenate([tv[:m], np.array([t0[ks]])])
        td2 = np.concatenate([td[:m], np.array([d0[ks]])])
        del tu, tv, td, t0, d0, pi
        LOG = max(1, int(n).bit_length())
        up = np.zeros((LOG, n), dtype=np.int64)
        mx = np.zeros((LOG, n), dtype=np.float32)
        depth = np.zeros(n, dtype=np.int64)
        stack = np.empty(n, dtype=np.int64)
        lca_build(tu2, tv2, td2, n - 1, LOG, up, mx, depth, stack, n)
        del tu2, tv2, td2
        rows = np.repeat(np.arange(n), KP)
        cols = Iknn.ravel()
        qo = np.empty(n * KP, dtype=np.float32)
        alpha_batch(up, mx, depth, LOG, rows, cols, Dknn.ravel(), qo, n * KP)
        Ak = qo.reshape(n, KP)
        del qo, rows, cols
        qu2 = np.repeat(np.arange(n), KD)
        qo2 = np.empty(n * KD, dtype=np.float32)
        alpha_batch(up, mx, depth, LOG, qu2, np.maximum(Wdel, 0).ravel(),
                    Ddel.ravel(), qo2, n * KD)
        Adel = np.where(Wdel != -1, qo2.reshape(n, KD), np.float32(np.inf))
        del qu2, qo2
        MMw = mw.shape[1]
        qu3 = np.repeat(np.arange(n), MMw)
        qo3 = np.empty(n * MMw, dtype=np.float32)
        alpha_batch(up, mx, depth, LOG, qu3, np.maximum(mw, 0).ravel(),
                    md.ravel(), qo3, n * MMw)
        Amst = np.where(mw != -1, qo3.reshape(n, MMw), np.float32(np.inf))
        del qu3, qo3
        del up, mx, depth, stack
    else:
        del tu, tv, td, t0, d0, pi, rows, cols
        Ak = Dknn
        Adel = Ddel
        Amst = md
    A0full = np.concatenate([Adel, Amst], axis=1)
    del Adel, Amst, Ddel
    # Union assembly, blocked to bound the (B,KP,W0) overlap transient:
    # All Delaunay + MST neighbors (deduplicated, Delaunay wins ties)
    # occupy each row head, then the closest KNN neighbors outside the
    # union fill to K. Rows end distance-ordered so the KC3 prefix
    # stays nearest.
    W0 = KD + mw.shape[1]
    tril = np.tril(np.ones((W0, W0), dtype=bool), -1)
    out = np.full((n, K), -1, dtype=np.int64)
    Ao = np.full((n, K), np.inf, dtype=np.float32)
    for a in range(0, n, CS):
        b = min(a + CS, n)
        M0 = np.concatenate([Wdel[a:b], mw[a:b]], axis=1)
        A0b = A0full[a:b]
        present = M0 != -1
        dup = present & np.any((M0[:, :, None] == M0[:, None, :]) & tril, axis=2)
        valid = present & ~dup
        nv = valid.sum(axis=1)
        rr = np.repeat(np.arange(a, b), nv)
        cc = (np.cumsum(valid, axis=1) - 1)[valid]
        out[rr, cc] = M0[valid]
        Ao[rr, cc] = A0b[valid]
        Wsb = Iknn[a:b]
        ov = np.any(Wsb[:, :, None] == out[a:b, None, :W0], axis=2)
        sel = ~ov
        Akb = Ak[a:b]
        oo2 = np.argsort(Akb, axis=1)
        selo = np.take_along_axis(sel, oo2, axis=1)
        need = K - nv
        keep_o = selo & (np.cumsum(selo, axis=1) <= need[:, None])
        take = np.zeros_like(sel)
        r3 = np.repeat(np.arange(a, b), keep_o.sum(axis=1))
        take[r3, oo2[keep_o]] = True
        r2 = np.repeat(np.arange(a, b), take.sum(axis=1))
        c2 = nv[r2] + np.cumsum(take, axis=1)[take] - 1
        out[r2, c2] = Wsb[take]
        Ao[r2, c2] = Akb[take]
        del M0, A0b, present, dup, valid, nv, rr, cc
        del Wsb, Akb, oo2, selo, need, keep_o, take, r3, r2, c2
    del Iknn, Dknn, Wdel, mw, Ak, A0full, tril
    oo = np.argsort(Ao, axis=1)
    cand = np.ascontiguousarray(np.take_along_axis(out, oo, axis=1))
    del out, Ao, oo
    return cand


def build_instance(n: int, seed: int):
    """Uniform points in the 1x1 square + Delaunay-first 30-NN
    candidate lists with Kruskal MST neighbors (KNN-only with
    self-padding at n <= 64) + sorted candidate edges for
    multi-fragment insertion.

    Coordinates are contiguous float64; candidates are contiguous int64.
    No O(n^2) distance matrix is ever built.
    """
    rng = np.random.default_rng(seed)
    coords = np.ascontiguousarray(rng.random((n, 2)), dtype=np.float64)
    if n > 64:
        mw, md = mst_neighbors(coords)
        cand = build_candidates_big(coords, mw, md)
        del mw, md
    else:
        cand = build_candidates_small(coords)
    eu, ev = build_edges(coords, cand)
    return coords, cand, eu, ev


def alloc_work(n: int):
    return (np.empty(n, dtype=np.int64),  # tour
            np.empty(n, dtype=np.int64),  # pos
            np.empty(n, dtype=np.int64),  # buf
            np.empty(n, dtype=np.int64),  # work (construction scratch)
            np.zeros(n, dtype=np.uint8),  # active (don't-look bits)
            np.empty(n, dtype=np.int64),  # parent (union-find)
            np.empty(n, dtype=np.int64),  # psize
            np.empty(n, dtype=np.int64),  # deg
            np.empty(n, dtype=np.int64),  # ea (neighbor 1)
            np.empty(n, dtype=np.int64))  # eb (neighbor 2)


def build_edges(coords, cand):
    """Length-sorted candidate edges as int32 pairs (setup, vectorized).

    Keeps every directed candidate edge (duplicates are skipped by the
    union guards); sort keys are float32 to halve transient memory.
    """
    n, kk = cand.shape
    if n >= 2 ** 31:
        raise ValueError("int32 edge arrays support n < 2**31")
    rows = np.repeat(np.arange(n, dtype=np.int64), kk)
    cols = cand.ravel()
    # Deduplicate: keep each undirected edge once via its lower-id end.
    # (Self-padding never survives this mask.) Residual duplicates are
    # skipped harmlessly by the union guards.
    keep = cols > rows
    eu = rows[keep].astype(np.int32)
    ev = cols[keep].astype(np.int32)
    dx = coords[eu, 0] - coords[ev, 0]
    dy = coords[eu, 1] - coords[ev, 1]
    dd = np.sqrt(dx * dx + dy * dy).astype(np.float32)
    del dx, dy
    order = np.argsort(dd)
    del dd
    return np.ascontiguousarray(eu[order]), np.ascontiguousarray(ev[order])


def solve_staged(coords, cand, eu, ev, seed=0):
    """Single exact-edge construction, light polish, then deep polish.

    One starting tour from the length-sorted candidate edges: no
    jittered rebuilds and no IPT merging (ablation measured no
    material beta contribution for either). L1 never exceeds the
    deterministic construction length L0. Returns (tour, init_len,
    init_s, final_len, opt_s, move_counts). Raises loudly if fragment
    patching or
    the final tour is invalid. `seed` drives the deterministic ILS
    kicks in deep_polish.
    """
    n = coords.shape[0]
    tour, pos, buf, work, active, parent, psize, deg, ea, eb = alloc_work(n)
    t0 = time.perf_counter()
    construct_initial(coords, cand, K, eu, ev, eu.shape[0], parent, psize,
                      deg, ea, eb, work, tour, pos, n)
    t1 = time.perf_counter()
    if not bool(np.all(deg == 2)):
        raise RuntimeError(f"fragment patching failed at n={n}")
    L0 = float(tour_len(coords, tour, n))
    lm2, lm3 = light_polish(coords, tour, pos, cand, active, buf, n)
    # Final deep polish on the single polished tour.
    dm2, dm3, dacc = deep_polish(coords, tour, pos, cand, active, buf, n,
                                 seed)
    t2 = time.perf_counter()
    L1 = float(tour_len(coords, tour, n))
    if np.bincount(tour, minlength=n).min() != 1:
        raise RuntimeError(f"invalid final tour at n={n}")
    return tour, L0, t1 - t0, L1, t2 - t1, (lm2 + dm2, lm3 + dm3, dacc)


def run_size(n: int, seed: int):
    t0 = time.perf_counter()
    coords, cand, eu, ev = build_instance(n, seed)
    setup_s = time.perf_counter() - t0
    tour, L0, init_s, L1, opt_s, moves = solve_staged(
        coords, cand, eu, ev, seed)
    b0 = L0 / math.sqrt(n)
    b1 = L1 / math.sqrt(n)
    return {"n": n, "setup_s": setup_s, "init_s": init_s, "opt_s": opt_s,
            "length": L1, "init_beta": b0, "final_beta": b1,
            "vs_opt_pct": 100.0 * (b1 / BHH - 1.0), "tour": tour,
            "moves": moves}


def _ask(prompt: str, default: str) -> str:
    """Prompt with a standard default; Enter accepts it, EOF takes it too."""
    try:
        ans = input(f"{prompt} [{default}]: ").strip()
    except EOFError:
        ans = ""
    return ans if ans else default


def _ask_int(prompt: str, default: int) -> int:
    try:
        return int(_ask(prompt, str(default)))
    except ValueError:
        print(f"  (using standard {default})")
        return default


def run_cases(num_cases: int, n: int, seed: int):
    """Edge-test driver: solve `num_cases` fresh 2D Euclidean instances
    at fixed n (per-case seeds derived deterministically from `seed`).
    The Python loop here is orchestration over instances only; every
    per-city/per-pair computation stays inside the compiled solver.
    """
    print("warming up (n=20 compile, untimed) ...")
    c0, k0, e0, v0 = build_instance(20, seed)
    solve_staged(c0, k0, e0, v0)
    print(f"{'case':>6} {'setup_s':>10} {'solve_s':>10} {'length':>12} "
          f"{'init_beta':>10} {'final_beta':>10}")
    tot_setup = 0.0
    tot_t = 0.0
    tot_b0 = 0.0
    tot_b1 = 0.0
    for i in range(num_cases):
        t0 = time.perf_counter()
        coords, cand, eu, ev = build_instance(n, seed + 1 + i)
        setup_s = time.perf_counter() - t0
        _, L0, init_s, L1, opt_s, moves = solve_staged(
            coords, cand, eu, ev, seed + 1 + i)
        b0 = L0 / math.sqrt(n)
        b1 = L1 / math.sqrt(n)
        tot_setup += setup_s
        tot_t += init_s + opt_s
        tot_b0 += b0
        tot_b1 += b1
        print(f"{i:>6} {setup_s:>10.3f} {init_s + opt_s:>10.3f} {L1:>12.3f} "
              f"{b0:>10.4f} {b1:>10.4f}")
        print(f"      moves: 2opt={moves[0]} 3opt={moves[1]} ils={moves[2]}")
    print(f"mean over {num_cases}: setup {tot_setup / num_cases:.3f}s, "
          f"solve {tot_t / num_cases:.3f}s, cycle {(tot_setup + tot_t) / num_cases:.3f}s, "
          f"init beta {tot_b0 / num_cases:.4f} -> final beta {tot_b1 / num_cases:.4f}")


def interactive():
    """Bare `python3 TSP.py`: ask for cases + nodes, then edge-test 2D
    Euclidean graphs with a fresh random seed (printed for reproducibility)."""
    print("TSP edge test — 2D Euclidean. Press Enter to accept each standard value.")
    num_cases = _ask_int("test cases", 10)
    n = _ask_int("nodes", 100)
    if n < 1:
        print("  (nodes must be >= 1; using standard 100)")
        n = 100
    seed = random.randint(0, 2 ** 31 - 1)
    print(f"graph: euclidean, n: {n}, seed: {seed}")
    run_cases(num_cases, n, seed)


def benchmark(sizes, seed: int):
    print("warming up (n=20 compile, untimed) ...")
    c0, k0, e0, v0 = build_instance(20, seed)
    solve_staged(c0, k0, e0, v0)
    print(f"{'n':>10} {'setup_s':>10} {'init_s':>10} {'opt_s':>10} {'length':>12} "
          f"{'init_beta':>10} {'final_beta':>10} {'vs 0.7124':>10}")
    results = []
    for n in sizes:
        r = run_size(n, seed + n)
        results.append(r)
        print(f"{r['n']:>10} {r['setup_s']:>10.2f} {r['init_s']:>10.2f} {r['opt_s']:>10.2f} "
              f"{r['length']:>12.3f} {r['init_beta']:>10.4f} {r['final_beta']:>10.4f} "
              f"{r['vs_opt_pct']:>+9.2f}%")
        print(f"      moves: 2opt={r['moves'][0]} 3opt={r['moves'][1]} "
              f"ils={r['moves'][2]}")
    return results


def selftest():
    print("warming up (n=20 compile, untimed) ...")
    c0, k0, e0, v0 = build_instance(20, 0)
    solve_staged(c0, k0, e0, v0)
    n = 300
    coords, cand, eu, ev = build_instance(n, 7)
    # 1. valid permutation starting at city 0
    tour, L0, _, L1, _, _ = solve_staged(coords, cand, eu, ev)
    assert tour[0] == 0 and sorted(tour.tolist()) == list(range(n)), "tour invalid"
    pos = np.empty(n, dtype=np.int64)
    pos[tour] = np.arange(n)
    assert bool(np.all(pos[tour] == np.arange(n))), "pos inverse broken"
    print("tour validity + pos inverse: OK")
    # 2. local search never worsens the constructed tour
    assert L1 <= L0 + 1e-9, (L1, L0)
    print(f"optimizer improves construction ({L0:.3f} -> {L1:.3f}): OK")
    # 3. determinism: identical length and tour on repeat
    tour2, _, _, L2, _, _ = solve_staged(coords, cand, eu, ev)
    assert L1 == L2 and bool(np.all(tour == tour2)), "nondeterministic"
    print("determinism across runs: OK")
    # 4. BHH sanity on uniform instance
    beta = L1 / math.sqrt(n)
    assert beta < 1.0, beta
    print(f"BHH ratio {beta:.4f} (< 1.0, vs 0.7124 optimum): OK")
    # 5. small-n boundaries: construction + 2/3-Opt stay crash-free
    # with valid tours (candidate self-padding covers thin rows).
    for ns in (1, 2, 3, 7, 8, 9, 12):
        cs, ks, es, vs = build_instance(ns, 1000 + ns)
        ts, _, _, _, _, _ = solve_staged(cs, ks, es, vs)
        assert ts[0] == 0 and sorted(ts.tolist()) == list(range(ns)), ns
    print("small-n boundaries (1..12) valid: OK")
    # 6. candidate rows hold K distinct real cities (no self/-1/dups),
    # so the Delaunay+MST+KNN union assembly is sound.
    assert bool(np.all(cand != np.arange(n)[:, None])), "self in candidates"
    assert bool(np.all(cand >= 0)), "pad in candidates"
    srt = np.sort(cand, axis=1)
    assert bool(np.all(srt[:, 1:] != srt[:, :-1])), "dup in candidates"
    print("candidate rows distinct and complete: OK")
    # 6b. every Delaunay neighbor is kept in its city's row, so
    # void-crossing bridges survive candidate filtering.
    tri6 = Delaunay(coords)
    ip6, ix6 = tri6.vertex_neighbor_vertices
    for vc in range(n):
        nb6 = set(ix6[ip6[vc]:ip6[vc + 1]].tolist())
        assert nb6 <= set(cand[vc].tolist()), vc
    del tri6, ip6, ix6
    print("delaunay neighbors all kept: OK")
    # 7. alpha mechanism exact on a 4-city unit square: MST = 3 sides,
    # diagonal alpha = sqrt(2) - 1, tree edges score 0.
    sq = np.array([[0., 0.], [1., 0.], [1., 1.], [0., 1.]])
    prs = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
    pu4 = np.array([a for a, _ in prs], dtype=np.int64)
    pv4 = np.array([b for _, b in prs], dtype=np.int64)
    pd4 = np.sqrt(((sq[pu4] - sq[pv4]) ** 2).sum(axis=1))
    o4 = np.argsort(pd4, kind="stable")
    pp4 = np.empty(4, dtype=np.int64)
    ps4 = np.empty(4, dtype=np.int64)
    tu4 = np.empty(4, dtype=np.int64)
    tv4 = np.empty(4, dtype=np.int64)
    td4 = np.empty(4, dtype=np.float64)
    m4 = mst_full(pu4[o4], pv4[o4], pd4[o4], 6, pp4, ps4, tu4, tv4, td4, 4, 3)
    assert m4 == 3, m4
    assert {tuple(sorted(p)) for p in zip(tu4[:3].tolist(), tv4[:3].tolist())} == {(0, 1), (0, 3), (1, 2)}
    LG4 = 3
    up4 = np.zeros((LG4, 4), dtype=np.int64)
    mx4 = np.zeros((LG4, 4), dtype=np.float32)
    dep4 = np.zeros(4, dtype=np.int64)
    stk4 = np.empty(4, dtype=np.int64)
    lca_build(tu4, tv4, np.ones(3, dtype=np.float32), 3, LG4, up4, mx4, dep4, stk4, 4)
    qa4 = np.array([0, 1, 1], dtype=np.int64)
    qb4 = np.array([2, 0, 3], dtype=np.int64)
    qd4 = np.array([np.sqrt(2), 1.0, np.sqrt(2)])
    qo4 = np.empty(3)
    alpha_batch(up4, mx4, dep4, LG4, qa4, qb4, qd4, qo4, 3)
    assert abs(qo4[0] - (np.sqrt(2) - 1)) < 1e-9, qo4
    assert abs(qo4[1]) < 1e-9, qo4
    assert abs(qo4[2] - (np.sqrt(2) - 1)) < 1e-9, qo4
    print("alpha mechanism exact on unit square: OK")
    # 8. ILS kick-recovery loop: valid tour, never worsens, deterministic.
    def _run_ils8():
        ci, ki, ei, vi = build_instance(100, 123)
        ti, pi8, bufi, worki, acti, pari, pszi, degi, eai, ebi = \
            alloc_work(100)
        construct_initial(ci, ki, K, ei, vi, ei.shape[0], pari, pszi,
                          degi, eai, ebi, worki, ti, pi8, 100)
        light_polish(ci, ti, pi8, ki, acti, bufi, 100)
        Li = float(tour_len(ci, ti, 100))
        ils_stage(ci, ti, pi8, ki, K, acti, bufi, 100, 1099)
        return ti, float(tour_len(ci, ti, 100)), Li

    ti1, Lii1, Li1 = _run_ils8()
    ti2, Lii2, Li2 = _run_ils8()
    assert sorted(ti1.tolist()) == list(range(100)), "ils broke tour"
    assert Lii1 <= Li1 + 1e-9, (Lii1, Li1)
    assert Lii1 == Lii2 and bool(np.all(ti1 == ti2)), "ils nondeterministic"
    print("ils kick loop valid, non-worsening, deterministic: OK")
    print("ALL SELFTESTS PASSED")


def main() -> None:
    p = argparse.ArgumentParser(
        description="TSP large-scale 2D Euclidean benchmark (compiled solver)")
    p.add_argument("cmd", nargs="?", default=None,
                   help="'selftest' for checks; omit to run the scale benchmark")
    p.add_argument("--sizes", nargs="+", type=int, default=SIZES)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()
    if args.cmd == "selftest":
        selftest()
    elif args.cmd is None:
        if len(sys.argv) == 1:
            interactive()
        else:
            benchmark(args.sizes, args.seed)
    else:
        p.error(f"unknown command {args.cmd!r}")


if __name__ == "__main__":
    main()
