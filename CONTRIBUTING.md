# Contributing to HRVSTCollection

Small, focused PRs welcome.

## Ground rules

* Browser demos stay dependency-free: vanilla HTML/CSS/JS, no build step, no CDN required.
* New Python code should be typed where touched and covered by an existing check (`selftest`, `demo.py`, or CI).
* Don't commit generated junk: `.venv/`, `__pycache__/`, `*.pyc`, `.DS_Store`, `*.log` are git-ignored. If one slips in, `git rm -r --cached` it.
* Keep data files small; large datasets live outside the repo.

## Workflow

1. Fork and branch from `main`.
2. Run `python -m py_compile` on every Python file you touch.
3. Open the affected Pages demo(s) locally and confirm no console errors.
4. Open a PR describing what changed and how you verified it.
