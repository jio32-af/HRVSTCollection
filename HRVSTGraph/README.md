# HRVSTGraph — Weighted-Grid Pathfinding Laboratory

Single-file, zero-dependency graph algorithm benchmarking lab.
Open `index.html` in any browser — no server, no packages, no build step.

## What it does

Benchmarks and visualizes four search algorithms on a shared **55×35 2D
weighted grid** rendered with HTML5 Canvas and plain vanilla JavaScript:

| Algorithm | Priority f(n) | Optimal? |
|---|---|---|
| Dijkstra's Algorithm | `g(n)` (uniform-cost) | Yes — baseline for the optimality ratio |
| A* Search | `g(n) + w·h(n)` | Yes, with admissible h and w = 1 |
| Greedy Best-First | `h(n)` (weight only rescales) | No — fastest, explores least |
| Bidirectional A* | `g(n) + w·h(n)` from both ends, alternating expansion, stops when `minF(fwd) + minF(bwd) ≥ best` | Yes, with admissible h and w = 1 |

## Grid model

- 8-directional movement. Diagonal step = terrain cost × √2.
- No corner cutting: a diagonal through two wall corners is forbidden.
- Terrain: **wall** (impassable), **mud** (×5 entry cost), **water** (×8),
  empty (×1). Paint with left-drag, erase with right-click or the erase brush.
- Drag the green **start** and red **target** nodes — the search re-runs live
  on every move when a result is on screen.

## Heuristics

Manhattan, Euclidean, Octile — plus a weight multiplier slider
`w ∈ [0, 3]`, so `f(n) = g(n) + w·h(n)`. Dijkstra ignores both controls.

## Mazes

- **Recursive Backtracker** (depth-first carving, long corridors)
- **Randomized Prim's** (frontier growth, bushy maze)
- Both carve on the odd lattice, add light braiding for loops, and reset
  start/target to opposite corners.
- **Scatter** adds random mud (10%) and water (6%) for weighted benchmarks.

## Modes

- **Visualize** — animated expansion with live telemetry.
- **Instant** — full result immediately (used for drag-live updates too).
- **Compare mode** — split-screen second board + challenger algorithm,
  side-by-side telemetry, and an A-vs-B table with the winner highlighted.
  Pressing RUN BOTH hides every panel (duel view: A left, B right) until both
  runs finish — Esc brings the panels back early.

## Telemetry (per run)

Nodes visited · frontier queue size (live + peak) · path cost ·
compute time in ms (pure search time, excludes animation) · optimality ratio
(`dijkstraCost ÷ pathCost`, from a silent Dijkstra baseline run on the same grid).

## Quick start (also built into the app via ? QUICK START)

1. **Paint** — pick wall, mud, or water, then drag on the grid. Right-click erases.
2. **Move** — drag the green start and red target anywhere, anytime.
3. **Run** — pick an algorithm and press VISUALIZE. Watch the telemetry panel.
4. **Experiment** — grow a BACKTRACKER maze, raise the weight slider, or tick
   Compare mode to race two algorithms head to head.

## Files

- `index.html` — the entire lab (markup, styles, engine). The only file that matters.
- `README.md` — this file.
