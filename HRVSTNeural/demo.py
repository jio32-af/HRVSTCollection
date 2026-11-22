"""HRVSTNeural demo: 8-character autoencoder in the browser.

The network is 296 - 64 - 32 - 16 - 8 - 16 - 32 - 64 - 296.
Eight characters from a 37 symbol alphabet (26 letters, 10 digits, space)
are one-hot encoded into 296 numbers, squeezed through 8 middle numbers,
and rebuilt. Training repeats until the rebuilt text matches exactly.

Run:  python3 demo.py [--port 8000]
Then open the printed address in a browser.
Press Control C to stop. Weights and log save on exit.
"""

import csv
import json
import os
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from network import HRVSTNet
from loss import mse

VOCAB = "abcdefghijklmnopqrstuvwxyz0123456789 "
NCHARS = 8
SIZES = [NCHARS * len(VOCAB), 64, 32, 16, 8, 16, 32, 64, NCHARS * len(VOCAB)]
LR = 0.5
SHOW_EVERY = 2500
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

lock = threading.Lock()
status = {"target": "", "output": "", "loss": 0.0, "steps": 0,
          "running": False, "matched": False}
saved = {"net": None, "rows": []}
run_id = {"n": 0}
stop_server = {"stop": False}

M2_STEPS = 2000
M2_LR = 0.001
M2_EVERY = 50

m2 = {"running": False, "done": False, "step": 0, "train_loss": 0.0,
      "test_loss": 0.0, "frozen": False, "curve": [],
      "message": "Idle"}
m2_nets = {"train": None, "frozen": None}
m2_run = {"n": 0}


def make_random_texts(n=500):
    return ["".join(random.choice(VOCAB) for _ in range(NCHARS))
            for _ in range(n)]


def mean_loss(net, vecs):
    total = 0.0
    for v in vecs:
        total += mse(net.forward(v), v)
    return total / len(vecs)


def train_once_worker(my_id, out_dir=OUT_DIR):
    random.seed(500)
    texts = make_random_texts(500)
    train_v = [encode(t) for t in texts[:450]]
    test_v = [encode(t) for t in texts[450:]]
    net = HRVSTNet(sizes=SIZES)
    with lock:
        m2_nets["train"] = net
        m2.update(running=True, done=False, step=0, curve=[],
                  train_loss=0.0, test_loss=0.0,
                  message="Training on 450 texts")
    curve = []
    for s in range(1, M2_STEPS + 1):
        if stop_server["stop"]:
            return
        with lock:
            if m2_run["n"] != my_id:
                return
        v = train_v[(s - 1) % len(train_v)]
        loss, _ = net.train_step(v, v, lr=M2_LR)
        if s % M2_EVERY == 0:
            curve.append(round(loss, 6))
            with lock:
                m2.update(step=s, train_loss=loss, curve=list(curve))
    train_loss = mean_loss(net, train_v)
    test_loss = mean_loss(net, test_v)
    os.makedirs(out_dir, exist_ok=True)
    net.save(os.path.join(out_dir, "weights500.json"))
    with open(os.path.join(out_dir, "mode2.json"), "w") as f:
        json.dump({"train_loss": train_loss, "test_loss": test_loss,
                   "curve": curve}, f)
    with lock:
        m2.update(running=False, done=True, step=M2_STEPS,
                  train_loss=train_loss, test_loss=test_loss,
                  curve=curve, message="Saved. Load it, then mirror.")


def start_train_once(out_dir=OUT_DIR):
    with lock:
        m2_run["n"] += 1
        my_id = m2_run["n"]
    worker = threading.Thread(target=train_once_worker,
                              args=(my_id, out_dir), daemon=True)
    worker.start()
    return True


def load_pretrained(out_dir=OUT_DIR):
    try:
        net = HRVSTNet.load(os.path.join(out_dir, "weights500.json"))
    except (OSError, ValueError, KeyError):
        with lock:
            m2.update(message="No saved weights yet. Train once first.")
        return False
    with lock:
        m2_nets["frozen"] = net
        m2.update(frozen=True,
                  message="Frozen weights loaded. No more training.")
    return True


def mirror_frozen(text):
    with lock:
        net = m2_nets["frozen"]
    if net is None:
        return {"error": "Load pretrained first."}
    target = clean(text)
    vec = encode(target)
    pred = net.forward(vec)
    out = decode(pred)
    loss = mse(pred, vec)
    with lock:
        status.update(output=out, loss=loss, steps=0, running=False,
                      matched=(out == target), target=target)
    return {"output": out, "loss": loss, "matched": out == target}


def clean(text):
    text = (text or "").lower()
    fixed = "".join(ch if ch in VOCAB else " " for ch in text)
    return (fixed + " " * NCHARS)[:NCHARS]


def encode(text):
    vec = [0.0] * (NCHARS * len(VOCAB))
    for i, ch in enumerate(clean(text)):
        vec[i * len(VOCAB) + VOCAB.index(ch)] = 1.0
    return vec


def decode(vec):
    out = ""
    for i in range(NCHARS):
        slot = vec[i * len(VOCAB):(i + 1) * len(VOCAB)]
        out += VOCAB[slot.index(max(slot))]
    return out


def random_text():
    return "".join(random.choice(VOCAB) for _ in range(NCHARS))


def train_worker(net, vec, target, my_id, rows):
    first = net.forward(vec)
    with lock:
        status.update(output=decode(first), steps=0, running=True,
                      matched=False, loss=0.0, target=target)
    steps = 0
    while not stop_server["stop"]:
        with lock:
            if run_id["n"] != my_id:
                return
        loss, pred = net.train_step(vec, vec, lr=LR)
        steps += 1
        if steps % SHOW_EVERY == 0:
            rows.append((steps, loss))
            with lock:
                status.update(output=decode(pred), loss=loss, steps=steps)
        if decode(pred) == target:
            rows.append((steps, loss))
            with lock:
                status.update(output=target, loss=loss, steps=steps,
                              running=False, matched=True)
            return


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>HRVST Letter Mirror</title>
<style>
body { background: #e6e6e6; font-family: system-ui, sans-serif; color: #222; margin: 0; }
.wrap { max-width: 900px; margin: 40px auto; padding: 0 20px; }
.card { background: #fff; border: 1px solid #ccc; border-radius: 8px; padding: 24px; }
h1 { font-size: 24px; margin: 0 0 4px; }
.sub { color: #555; margin: 0 0 16px; }
.row { display: flex; gap: 12px; margin-bottom: 16px; }
#text { flex: 1; font-size: 22px; padding: 10px; letter-spacing: 2px; }
button { font-size: 16px; padding: 10px 16px; }
.stage { display: flex; gap: 16px; align-items: stretch; }
#output { flex: 1; font-size: 52px; letter-spacing: 6px; min-height: 90px;
  border: 1px solid #ccc; border-radius: 8px; padding: 12px;
  background: #fafafa; overflow-x: auto; white-space: nowrap; }
.side { min-width: 150px; }
.num { font-size: 28px; }
.lbl { color: #555; font-size: 14px; margin-top: 8px; }
.meta { margin-top: 12px; color: #333; }
.modes { color: #555; font-size: 14px; margin: 0 0 16px; }
.m2 { margin-top: 20px; border-top: 1px solid #ccc; padding-top: 16px; }
h2 { font-size: 18px; margin: 0 0 8px; }
#curve { border: 1px solid #ccc; background: #fafafa; }
</style>
</head>
<body>
<div class="wrap"><div class="card">
<h1>HRVST Letter Mirror</h1>
<p class="sub">Type 8 letters, numbers, or spaces. The network learns to copy it exactly.</p>
<p class="modes">Mode 1 = overfit one text. Mode 2 = frozen test.</p>
<div class="row">
<input id="text" maxlength="8" value="hello 12">
<button id="trainbtn" onclick="mainAction()">Train this text</button>
<button onclick="send(true)">Random text</button>
</div>
<div class="stage">
<div id="output"></div>
<div class="side">
<div class="lbl">Loss</div><div class="num" id="loss">-</div>
<div class="lbl">Total steps</div><div class="num" id="steps">0</div>
</div>
</div>
<div class="meta" id="status">Idle</div>
<div class="m2">
<h2>Frozen test</h2>
<div class="row">
<button onclick="trainOnce()">Train once on 500 texts</button>
<button onclick="loadPre()">Load pretrained</button>
</div>
<div class="meta" id="m2status">Idle</div>
<div class="meta">Train loss <span id="m2train">-</span> Test loss <span id="m2test">-</span></div>
<svg id="curve" width="100%" height="160"></svg>
</div>
</div></div>
<script>
async function send(useRandom) {
  let t = document.getElementById("text").value;
  if (useRandom) {
    t = (await (await fetch("/random")).json()).text;
    document.getElementById("text").value = t;
  }
  await fetch("/set", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({text: t})});
}
async function trainOnce() {
  await fetch("/train_once", {method: "POST"});
}
async function loadPre() {
  await fetch("/load_pretrained", {method: "POST"});
}
async function mirror() {
  const t = document.getElementById("text").value;
  await fetch("/mirror_frozen", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({text: t})});
}
let isFrozen = false;
async function mainAction() {
  if (isFrozen) await mirror();
  else await send(false);
}
function drawCurve(c) {
  const svg = document.getElementById("curve");
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  if (!c.length) return;
  const W = svg.clientWidth || 800, H = 160;
  const lo = Math.min(...c), hi = Math.max(...c), sp = (hi - lo) || 1;
  const pts = c.map((v, i) =>
    (56 + (W - 76) * i / Math.max(1, c.length - 1)).toFixed(1) + "," +
    (12 + (H - 56) * (1 - (v - lo) / sp)).toFixed(1)).join(" ");
  const pl = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  pl.setAttribute("points", pts);
  pl.setAttribute("fill", "none");
  pl.setAttribute("stroke", "#1d4d8d");
  pl.setAttribute("stroke-width", "2");
  svg.appendChild(pl);
}
async function poll() {
  const s = await (await fetch("/status")).json();
  document.getElementById("output").textContent = s.output;
  document.getElementById("loss").textContent = s.loss.toFixed(4);
  document.getElementById("steps").textContent = s.steps;
  document.getElementById("status").textContent =
    s.matched ? "Matched exactly" : (s.running ? "Training" : "Idle");
  const m = s.m2 || {};
  isFrozen = !!m.frozen;
  document.getElementById("trainbtn").textContent =
    isFrozen ? "Test" : "Train this text";
  document.getElementById("m2status").textContent = m.running ?
    ("Training step " + m.step + " of 2000") : (m.message || "Idle");
  document.getElementById("m2train").textContent =
    (m.train_loss || 0).toFixed(4);
  document.getElementById("m2test").textContent =
    (m.test_loss || 0).toFixed(4);
  drawCurve(m.curve || []);
}
setInterval(poll, 500);
poll();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body, kind="text/html"):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self._send(PAGE)
        elif self.path == "/status":
            with lock:
                snap = dict(status)
                snap["m2"] = dict(m2)
            self._send(json.dumps(snap), "application/json")
        elif self.path == "/random":
            self._send(json.dumps({"text": random_text()}), "application/json")
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/train_once":
            self._send(json.dumps({"started": start_train_once()}),
                        "application/json")
            return
        if self.path == "/load_pretrained":
            self._send(json.dumps({"loaded": load_pretrained()}),
                        "application/json")
            return
        if self.path == "/mirror_frozen":
            length = int(self.headers.get("Content-Length", 0))
            try:
                text = json.loads(self.rfile.read(length) or b"{}").get("text", "")
            except Exception:
                text = ""
            self._send(json.dumps(mirror_frozen(text)), "application/json")
            return
        if self.path != "/set":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            text = json.loads(self.rfile.read(length) or b"{}").get("text", "")
        except Exception:
            text = ""
        target = clean(text)
        vec = encode(target)
        net = HRVSTNet(sizes=SIZES)
        rows = []
        with lock:
            run_id["n"] += 1
            my_id = run_id["n"]
            saved["net"] = net
            saved["rows"] = rows
        worker = threading.Thread(target=train_worker,
                                  args=(net, vec, target, my_id, rows),
                                  daemon=True)
        worker.start()
        self._send(json.dumps({"target": target}), "application/json")


def save_all(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with lock:
        net = saved["net"]
        rows = list(saved["rows"])
    if net is not None:
        net.save(os.path.join(out_dir, "weights.json"))
    with open(os.path.join(out_dir, "loss.csv"), "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["step", "raw"])
        for step, raw in rows:
            writer.writerow([step, f"{raw:.6f}"])


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()
    server = None
    for port in range(args.port, args.port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit("No free port found.")
    print(f"Open http://127.0.0.1:{server.server_port} in a browser.")
    print("Press Control C to stop. Weights and log save on exit.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        save_all(args.out)
        server.server_close()
        print("Saved weights and log.")
