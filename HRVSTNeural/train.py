import argparse
import csv
import os
import random
import signal

from network import HRVSTNet
from loss import LossTracker

LOG_EVERY = 100

stop_flag = {"stop": False}


def on_signal(signum, frame):
    stop_flag["stop"] = True


def make_data(n=32, seed=0):
    rnd = random.Random(seed)
    data = []
    for _ in range(n):
        x = [rnd.uniform(-1.0, 1.0) for _ in range(4)]
        y = [(x[0] + x[1]) / 2.0, (x[2] - x[3]) / 2.0]
        data.append((x, y))
    return data


def polyline(steps, points, w, h, lo, hi, color, width):
    span = (hi - lo) if hi > lo else 1.0
    s0, s1 = steps[0], steps[-1]
    sspan = (s1 - s0) if s1 > s0 else 1
    pts = []
    for s, v in zip(steps, points):
        px = 56 + (w - 76) * ((s - s0) / sspan)
        py = 12 + (h - 56) * (1.0 - (v - lo) / span)
        pts.append(f"{px:.1f},{py:.1f}")
    return f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="{width}" />'


def write_graph(path, steps, raw, smooth):
    w, h = 900, 420
    vals = raw + smooth
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.1 or 0.1
    lo -= pad
    hi += pad
    html = f"""<!doctype html>
<meta charset="utf-8">
<title>Training Loss</title>
<style>
body {{ font-family: system-ui, sans-serif; color: #111; margin: 24px; }}
.panel {{ max-width: 980px; }}
.controls button {{ margin-right: 8px; }}
.chart {{ overflow: auto; border: 1px solid #bbb; max-height: 70vh; }}
svg {{ display: block; }}
.meta {{ color: #333; }}
</style>
<div class="panel">
<h2>Training Loss</h2>
<p class="meta">8 layer network. Steps logged every 100. First raw {raw[0]:.4f}. Last raw {raw[-1]:.4f}. Last smoothed {smooth[-1]:.4f}.</p>
<div class="controls">
<button id="zin">Zoom in</button>
<button id="zout">Zoom out</button>
<button id="reset">Reset view</button>
</div>
<div class="chart" id="chart">
<svg id="plot" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
<text x="56" y="{h - 8}" font-size="12">Training step</text>
<text x="6" y="20" font-size="12">Loss value</text>
{polyline(steps, raw, w, h, lo, hi, "#1d4ed8", 2)}
{polyline(steps, smooth, w, h, lo, hi, "#15803d", 2)}
</svg>
</div>
<p><span style="color:#1d4ed8">Blue line: raw loss</span><br><span style="color:#15803d">Green line: smoothed loss</span></p>
<p class="meta">Scroll sideways inside the frame. Use the buttons or the mouse wheel to zoom. Drag to move.</p>
</div>
<script>
let z = 1;
const svg = document.getElementById("plot");
const baseW = {w}, baseH = {h};
function apply() {{ svg.setAttribute("width", baseW * z); svg.setAttribute("height", baseH * z); }}
document.getElementById("zin").onclick = () => {{ z = Math.min(6, z * 1.25); apply(); }};
document.getElementById("zout").onclick = () => {{ z = Math.max(0.5, z / 1.25); apply(); }};
document.getElementById("reset").onclick = () => {{ z = 1; apply(); }};
document.getElementById("chart").addEventListener("wheel", (e) => {{
  e.preventDefault();
  z = Math.min(6, Math.max(0.5, z + (e.deltaY < 0 ? 0.15 : -0.15)));
  apply();
}}, {{ passive: false }});
</script>
"""
    with open(path, "w") as f:
        f.write(html)


def save_all(out_dir, net, rows):
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "loss.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["step", "raw", "smooth"])
        for step, r, sm in rows:
            w.writerow([step, f"{r:.6f}", f"{sm:.6f}"])
    html_path = os.path.join(out_dir, "loss.html")
    steps = [r[0] for r in rows]
    write_graph(html_path, steps, [r[1] for r in rows], [r[2] for r in rows])
    weights_path = os.path.join(out_dir, "weights.json")
    net.save(weights_path)
    return csv_path, html_path, weights_path


def train(steps=0, lr=0.01, seed=1, out_dir="logs"):
    random.seed(seed)
    net = HRVSTNet()
    tracker = LossTracker(decay=0.997)
    data = make_data()
    rows = []
    s = 0
    bounded = steps and steps > 0
    try:
        while True:
            if bounded and s >= steps:
                break
            if stop_flag["stop"]:
                break
            x, y = data[s % len(data)]
            raw, _ = net.train_step(x, y, lr=lr)
            sm = tracker.update(raw)
            if s % LOG_EVERY == 0:
                rows.append((s, raw, sm))
            s += 1
    except KeyboardInterrupt:
        pass
    if not rows:
        rows.append((0, tracker.smooth if tracker.smooth is not None else 0.0,
                     tracker.smooth if tracker.smooth is not None else 0.0))
    csv_path, html_path, weights_path = save_all(out_dir, net, rows)
    first = rows[0][1]
    last = rows[-1][1]
    return {
        "csv": csv_path, "html": html_path, "weights": weights_path,
        "first": first, "last": last, "smooth": rows[-1][2],
        "logged_steps": len(rows), "ran_steps": s,
    }


if __name__ == "__main__":
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    ap = argparse.ArgumentParser(description="Train the 8 layer network until stopped")
    ap.add_argument("--steps", type=int, default=0, help="Bounded step count. Zero runs until you stop it.")
    ap.add_argument("--lr", type=float, default=0.01)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="logs")
    a = ap.parse_args()
    print("Running. Press Control C to stop. Progress saves on exit.")
    try:
        res = train(steps=a.steps, lr=a.lr, seed=a.seed, out_dir=a.out)
    finally:
        pass
    print(f"ran steps={res['ran_steps']} logged rows={res['logged_steps']}")
    print(f"first={res['first']:.4f} last={res['last']:.4f} smooth={res['smooth']:.4f}")
    print(f"csv={res['csv']}")
    print(f"html={res['html']}")
    print(f"weights={res['weights']}")
