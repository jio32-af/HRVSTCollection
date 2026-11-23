# HRVSTSim — N-Body Gravitational Astrophysics Simulator

A complete, **standalone** N-body gravity simulator in a single `index.html`.
Pure HTML5 Canvas + vanilla JavaScript. No libraries, no physics engines,
no network requests — double-click `index.html` and it runs offline.

## Run it

Open `HRVSTSim/index.html` in any modern browser. Nothing to install.

## What it implements

- **Two force solvers**
  - `Exact O(N²)` — direct pairwise summation (ground truth).
  - `Barnes–Hut O(N log N)` — quadtree approximation with an interactive
    **θ (theta) accuracy slider** (0–1.5). θ = 0 opens every cell, which
    reproduces the exact solver; larger θ groups distant cells and runs faster.
- **Velocity Verlet integrator** (symplectic, 2nd order) — position update
  uses `x += v·dt + a·dt²/2`, then forces are recomputed and velocity is
  finished with `v += (a_old + a_new)·dt/2`. Symplectic = orbital energy
  oscillates instead of drifting away, so orbits stay stable for a long time.
- **Live quadtree overlay** — every rendered frame draws the recursive
  bounding boxes of the tree built during the last force evaluation
  (plus a dot at each internal cell's center of mass). Switch to Exact mode
  and the overlay reports "— (exact)" since no tree is built.
- **Slingshot launching** — in Slingshot mode, press-drag-release anywhere:
  the anchor is the spawn point, pulling back sets the velocity vector
  (arrow + live `|v|` readout). Choose **Body**, **Star**, or **Black hole**
  and a mass first (`M` / `S` / `B` keys switch type).
- **Presets** — Figure-8 three-body choreography (C. Moore's periodic
  solution, scaled from dimensionless units), Binary star system, Solar
  system, Colliding galaxies (two counter-rotating disks around black holes,
  142 bodies — the case where Barnes–Hut shines). Presets set tuned G and
  dt values, which you can then change freely.
- **Adjustable timestep dt, gravitational constant G, steps/frame.**
- **Live telemetry** — kinetic E, potential E, total E, energy drift % vs. a
  baseline (set on preset load; re-baseline anytime), active body count
  (with merged-away count), tree node count, FPS / steps-per-second.

Extras: orbit trails, inelastic collision merging (momentum-conserving,
black-hole type wins), pan/zoom camera, pause + single-step.

## Physics notes

- Softening ε = 3 px (`SOFT2 = 9`): forces use `1/(r² + ε²)^{3/2}` and the
  potential uses `1/sqrt(r² + ε²)`, so close encounters never divide by zero.
- Circular-orbit preset speeds use `v = √(G·M/r)`; the binary uses
  `v = √(G·m/2d)` for separation `d`; the figure-8 scales the exact
  dimensionless solution by `v_sim = v_dim·√(G·m/L)` with `L = 120`.
- Adding bodies changes total energy honestly — drift % is measured against
  the baseline, so launching a planet moves the drift readout. Hit
  "Reset energy baseline" after editing to start a fresh measurement.
- The BH force walk always opens cells containing the target body, so a body
  never feels its own mass through an approximation.

## Keybinds

| Keys | Action |
|---|---|
| `Space` / `P` | pause / resume |
| `R` | restart current preset |
| `1`–`4` | load figure-8 / binary / solar / galaxies |
| `M` / `S` / `B` | new body is a planet-mass body / star / black hole |
| `L` / `V` | slingshot-launch mode / pan mode |
| `E` | toggle solver (exact / Barnes–Hut) |
| `[` / `]` | theta down / up |
| `T` / `Q` / `C` | toggle trails / quadtree overlay / merging |
| `F` | fit / reset view |
| `Esc` | cancel slingshot aim |

All keybinds are also listed in the panel inside the app.

## Files (everything lives in this folder)

- `index.html` — the whole simulator (markup + CSS + JS, no dependencies).
- `README.md` — this file.

## Verification

Physics core is DOM-free, so it was validated by mirroring the exact
algorithms in Python: exact-vs-BH(θ=0) agreement, Barnes–Hut error shrinking
with θ, Verlet energy conservation on a circular orbit, momentum conservation
in mergers, and all four preset initial conditions (zero total momentum,
circular-orbit speeds). The artifact was also checked for balanced
braces/parens and zero external references.
