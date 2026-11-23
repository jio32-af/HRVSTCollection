# HRVSTWave — 2D Wave Physics Simulator

Interactive 2D **damped wave equation** solver in a single standalone file.
Open [`index.html`](./index.html) in any browser — no build step, no dependencies,
no internet needed.

## The physics

Solved PDE (damped wave equation):

```
∂²u/∂t² = c²∇²u − γ ∂u/∂t
```

Discretized with a second-order-central **Finite-Difference Time-Domain (FDTD)**
scheme (`d = γΔt/2`, `C = cΔt/Δx`):

```
u_next = ( 2u − u_prev·(1−d) + C²·(uL+uR+uU+uD − 4u) ) / (1+d)
```

Walls are **Dirichlet boundaries** (`u = 0` on wall cells), so waves reflect off
them. The rim of the grid is pinned to 0 (open boundary). Stability requires
`C ≤ 1/√2 ≈ 0.707`; the speed slider caps at `0.50`, so the scheme is
unconditionally stable in practice. Continuous sources are hard-driven as
`u = A·sin(2πft)` at their grid point.

## Tools (left panel)

| Tool (left icon rail, keys `1`–`5`) | Action |
|---|---|
| Pulse | Click drops a Gaussian wave packet; drag sprinkles |
| Emitter | Click places a continuous sinusoidal source (frequency/amplitude sliders retune live sources too) |
| Wall | Drag paints solid reflective barriers |
| Slit barrier | Drag a line — a wall is stamped along it with two gaps at ⅓ and ⅔ |
| Eraser | Drag removes walls and nearby emitters |
| +SLIT | One-click vertical double-slit barrier at grid centre |

Plus sliders for **brush size, frequency (0.2–8 Hz sim-time), amplitude,
wave speed, damping**, three **palettes** (height-map / energy-intensity /
phase-interference), three **grid resolutions** (192×120, 288×180, 384×240),
and transport controls (pause, clear waves, reset all).

## Preset experiments

1. **Double-Slit Interference** — coherent source + two-slit barrier, view in
   the phase palette to see fringe nodes as dark bands.
2. **Whispering Gallery Ellipse** — a pulse fired at one focus of a reflective
   ellipse refocuses at the other focus.
3. **Ripple Tank** — two coherent point sources in an open low-loss tank,
   best in the energy palette.

Keyboard: `1–5` tools, `Space` pause, `C` clear waves.

## HUD

Floating glass chips over the viewport show **FPS + sparkline**, physics
**step count**, **grid resolution**, mean **wave energy** (Σu²/N, with
sparkline), **peak amplitude**, estimated **wavelength λ** in cells, hovered
**cell coordinates** with crosshair, and Courant number `c`. The inspector
adds a **CFL bar** (stable while C ≤ 1/√2). View toggles: **bloom glow** and
**grid overlay**.

## Files

Everything for this project lives in this folder:

- `index.html` — the complete simulator (markup + styles + solver, inline)
- `README.md` — this documentation

## Implementation notes

- Three `Float32Array` fields (`u`, `u_prev`, `u_next`) rotate each step; one
  `Uint8Array` wall mask; 2 physics substeps per animation frame.
- Rendering rasterizes the field into a 2× supersampled display buffer (bilinear
  interpolation through a 1024-entry LUT per palette) for smooth, non-pixelated
  waves, plus a soft bloom underlay; walls, emitters, and slit previews are
  crisp vector overlays drawn at double device resolution.
- Display gain is adaptive (`peakSmooth` decay) so interference stays visible
  as waves damp out.
