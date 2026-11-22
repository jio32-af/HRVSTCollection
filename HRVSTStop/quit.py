#!/usr/bin/env python3
"""HRVSTStop / quit.py — pick running apps, pick minutes, auto-quit them.

Run:
    python3 quit.py [--port 8137]

Then open the printed http://127.0.0.1:PORT address in a browser.
Stdlib only. Works best on macOS, degrades gracefully on Linux.
"""

import argparse
import datetime
import json
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

JOBS = {}
JOBS_LOCK = threading.Lock()
JOB_SEQ = 0


def list_running_apps():
    """Return a sorted list of running application names."""
    # macOS: ask System Events for GUI process names.
    try:
        out = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of every process whose background only is false'],
            capture_output=True, text=True, timeout=8,
        )
        if out.returncode == 0 and out.stdout.strip():
            names = [p.strip() for p in out.stdout.strip().split(",")]
            seen, apps = set(), []
            for n in names:
                if n and n not in seen:
                    seen.add(n)
                    apps.append(n)
            if apps:
                return sorted(apps, key=str.lower)
    except Exception:
        pass
    # Fallback: unique process command names via ps.
    try:
        out = subprocess.run(["ps", "-axo", "comm="],
                             capture_output=True, text=True, timeout=8)
        seen, apps = set(), []
        for line in out.stdout.splitlines():
            base = line.strip().split("/")[-1].strip()
            if not base or base.startswith("(") or base in seen:
                continue
            if base in ("ps", "quit.py", "Python"):
                continue
            seen.add(base)
            apps.append(base)
        return sorted(apps, key=str.lower)[:200]
    except Exception:
        return []


def quit_app(name):
    """Try a graceful quit, then fall back to terminating the process."""
    name = name.strip()
    if not name:
        return False, "empty name"
    # Graceful AppleScript quit (macOS).
    try:
        r = subprocess.run(
            ["osascript", "-e", f'tell application "{name}" to quit'],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return True, "quit via AppleScript"
    except Exception as e:
        last_err = str(e)
    else:
        last_err = r.stderr.strip() or "AppleScript quit failed"
    # Fallback: pkill / pkill -x.
    for args in (["pkill", "-x", name], ["pkill", "-f", name]):
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return True, f"quit via {' '.join(args)}"
        except Exception:
            continue
    return False, last_err


def fire_job(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or job["status"] != "pending":
            return
    results = {}
    for app in job["apps"]:
        ok, msg = quit_app(app)
        results[app] = {"ok": ok, "detail": msg}
    with JOBS_LOCK:
        job["status"] = "done"
        job["finished_at"] = time.time()
        job["results"] = results


def schedule_jobs(apps, minutes):
    global JOB_SEQ
    if not apps:
        raise ValueError("Pick at least one application.")
    if not (minutes > 0 and minutes <= 24 * 60):
        raise ValueError("Minutes must be between 0 and 1440.")
    cleaned = sorted({a.strip() for a in apps if a and a.strip()},
                     key=str.lower)
    if not cleaned:
        raise ValueError("Pick at least one application.")
    delay = minutes * 60.0
    with JOBS_LOCK:
        JOB_SEQ += 1
        job_id = JOB_SEQ
        JOBS[job_id] = {
            "id": job_id,
            "apps": cleaned,
            "minutes": minutes,
            "fires_at": time.time() + delay,
            "status": "pending",
            "results": {},
        }
    t = threading.Timer(delay, fire_job, args=(job_id,))
    t.daemon = True
    t.start()
    with JOBS_LOCK:
        JOBS[job_id]["timer"] = t
    return JOBS[job_id]


def job_public(job):
    fires = datetime.datetime.fromtimestamp(job["fires_at"])
    return {
        "id": job["id"],
        "apps": job["apps"],
        "minutes": job["minutes"],
        "fires_at": job["fires_at"],
        "fires_at_local": fires.strftime("%H:%M:%S"),
        "seconds_left": max(0, int(job["fires_at"] - time.time())),
        "status": job["status"],
        "results": job.get("results", {}),
    }


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HRVSTStop</title>
<style>
  :root { --ink:#0a0a0a; --paper:#ffffff; --line:#0a0a0a; --soft:#f2f2f2; }
  * { box-sizing:border-box; }
  body {
    margin:0; background:var(--paper); color:var(--ink);
    font-family:"Helvetica Neue",-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Verdana,sans-serif;
    font-weight:800; letter-spacing:-0.01em;
    display:flex; justify-content:center; padding:48px 16px;
  }
  .card { width:min(560px,100%); border:3px solid var(--ink); border-radius:18px; padding:28px; }
  h1 { margin:0; font-size:44px; line-height:1; font-weight:900; letter-spacing:-0.04em; }
  p.sub { margin:8px 0 20px; font-size:14px; font-weight:700; }
  .mode { display:flex; border:3px solid var(--ink); border-radius:999px; overflow:hidden; margin-bottom:16px; }
  .mode button { flex:1; border:0; background:#fff; color:#0a0a0a; font:inherit; font-size:15px; padding:10px; cursor:pointer; }
  .mode button.on { background:#0a0a0a; color:#fff; }
  label.t { display:block; font-size:13px; font-weight:900; text-transform:uppercase; letter-spacing:.08em; margin:16px 0 8px; }
  .drop { position:relative; }
  .dropbtn { width:100%; border:3px solid var(--ink); border-radius:12px; background:#fff;
    font:inherit; font-size:16px; text-align:left; padding:12px 14px; cursor:pointer; }
  .panel { display:none; position:absolute; z-index:5; left:0; right:0; top:calc(100% + 8px);
    background:#fff; border:3px solid var(--ink); border-radius:12px; max-height:260px; overflow:auto; }
  .panel.open { display:block; }
  .search { width:100%; border:0; border-bottom:3px solid var(--ink); padding:10px 12px; font:inherit; font-size:14px; outline:none; }
  .opt { display:flex; gap:10px; align-items:center; padding:10px 12px; font-size:15px; cursor:pointer; }
  .opt:hover { background:var(--soft); }
  .opt input { width:18px; height:18px; accent-color:#0a0a0a; }
  .chips { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
  .chip { border:3px solid var(--ink); border-radius:999px; padding:4px 6px 4px 12px; font-size:13px;
    display:flex; gap:8px; align-items:center; background:#0a0a0a; color:#fff; }
  .chip button { border:0; border-radius:50%; width:22px; height:22px; font-weight:900; cursor:pointer; }
  .row { display:flex; gap:12px; }
  input.num { flex:1; border:3px solid var(--ink); border-radius:12px; padding:12px 14px; font:inherit; font-size:18px; }
  button.go { flex:1; border:3px solid var(--ink); border-radius:12px; background:#0a0a0a; color:#fff;
    font:inherit; font-size:18px; font-weight:900; cursor:pointer; }
  button.go:hover { background:#2a2a2a; }
  #msg { min-height:22px; font-size:14px; margin-top:12px; }
  .job { border:3px solid var(--ink); border-radius:12px; padding:10px 12px; margin-top:10px; font-size:14px; }
  .job.done { background:var(--soft); }
  .job button { float:right; border:2px solid var(--ink); background:#fff; border-radius:8px; font:inherit; font-size:12px; cursor:pointer; }
</style>
</head>
<body>
<div class="card">
  <h1>HRVSTStop</h1>
  <p class="sub">Pick apps. Pick minutes. They quit on time.</p>
  <div class="mode">
    <button id="mSingle" class="on">Single choice</button>
    <button id="mMulti">Multi choice</button>
  </div>
  <label class="t">Running applications</label>
  <div class="drop">
    <button class="dropbtn" id="dropbtn">Select application(s) &#9662;</button>
    <div class="panel" id="panel">
      <input class="search" id="search" placeholder="Type to filter">
      <div id="opts"><div class="opt">Loading…</div></div>
    </div>
  </div>
  <div class="chips" id="chips"></div>
  <label class="t">Quit after (minutes)</label>
  <div class="row">
    <input class="num" id="mins" type="number" min="0.1" max="1440" step="0.5" value="15">
    <button class="go" id="go">Quit timer</button>
  </div>
  <div id="msg"></div>
  <label class="t">Scheduled</label>
  <div id="jobs"><div class="job">Nothing scheduled yet.</div></div>
</div>
<script>
let apps = [], picked = [], multi = false;
const $ = id => document.getElementById(id);
function renderBtn(){ $('dropbtn').textContent = picked.length ? picked.join(', ') + ' \\u25BE' : 'Select application(s) \\u25BE'; }
function renderChips(){
  $('chips').innerHTML = picked.map(a =>
    `<span class="chip">${a}<button data-a="${a.replace(/"/g,'&quot;')}">x</button></span>`).join('');
  document.querySelectorAll('.chip button').forEach(b =>
    b.onclick = () => { picked = picked.filter(x => x !== b.dataset.a); sync(); });
}
function renderOpts(filter=''){
  const f = filter.toLowerCase();
  $('opts').innerHTML = apps.filter(a => a.toLowerCase().includes(f)).map(a =>
    `<label class="opt"><input type="checkbox" value="${a.replace(/"/g,'&quot;')}" ${picked.includes(a)?'checked':''}>${a}</label>`).join('') || '<div class="opt">No match</div>';
  document.querySelectorAll('#opts input').forEach(c => c.onchange = () => {
    if (c.checked) picked = multi ? [...new Set([...picked, c.value])] : [c.value];
    else picked = picked.filter(x => x !== c.value);
    sync();
  });
}
function sync(){ renderBtn(); renderChips(); renderOpts($('search').value); }
$('dropbtn').onclick = () => $('panel').classList.toggle('open');
$('search').oninput = e => renderOpts(e.target.value);
$('mSingle').onclick = () => { multi=false; picked=picked.slice(0,1);
  $('mSingle').classList.add('on'); $('mMulti').classList.remove('on'); sync(); };
$('mMulti').onclick = () => { multi=true;
  $('mMulti').classList.add('on'); $('mSingle').classList.remove('on'); sync(); };
$('go').onclick = async () => {
  const minutes = parseFloat($('mins').value);
  $('msg').textContent = '';
  const r = await fetch('/api/schedule', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({apps:picked, minutes})});
  const j = await r.json();
  $('msg').textContent = j.ok ? `Timer set: ${j.job.apps.join(', ')} in ${j.job.minutes} min.` : ('Error: ' + (j.error||'failed'));
  if (j.ok) loadJobs();
};
async function loadApps(){
  const r = await fetch('/api/apps'); const j = await r.json();
  apps = j.apps || []; renderOpts();
}
async function loadJobs(){
  const r = await fetch('/api/jobs'); const j = await r.json();
  $('jobs').innerHTML = j.jobs.length ? j.jobs.map(q =>
    `<div class="job ${q.status}"><button data-id="${q.id}">cancel</button><b>#${q.id}</b> ${q.apps.join(', ')}<br>${q.status} · ${q.seconds_left}s left · fires ${q.fires_at_local}</div>`).join('')
    : '<div class="job">Nothing scheduled yet.</div>';
  document.querySelectorAll('.job button').forEach(b => b.onclick = async () => {
    await fetch('/api/cancel', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id: parseInt(b.dataset.id)})});
    loadJobs();
  });
}
loadApps(); loadJobs(); setInterval(loadJobs, 5000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/apps":
            self.send_json({"apps": list_running_apps()})
        elif path == "/api/jobs":
            with JOBS_LOCK:
                jobs = [job_public(j) for j in sorted(JOBS.values(), key=lambda j: j["id"])]
            self.send_json({"jobs": jobs})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self.send_json({"ok": False, "error": "invalid JSON"}, 400)
        if path == "/api/schedule":
            try:
                job = schedule_jobs(data.get("apps", []),
                                    float(data.get("minutes", 0)))
            except (ValueError, TypeError) as e:
                return self.send_json({"ok": False, "error": str(e)}, 400)
            return self.send_json({"ok": True, "job": job_public(job)})
        if path == "/api/cancel":
            with JOBS_LOCK:
                job = JOBS.get(int(data.get("id", -1)))
                if job and job["status"] == "pending":
                    job["status"] = "cancelled"
                    job.get("timer", threading.Timer(0, lambda: None)).cancel()
                    return self.send_json({"ok": True})
            return self.send_json({"ok": False, "error": "job not found"}, 404)
        return self.send_json({"error": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser(description="HRVSTStop: auto-quit apps after N minutes.")
    ap.add_argument("--port", type=int, default=8137)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"HRVSTStop running at {url}")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
