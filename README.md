# HRVSTCollection — by Satvik, 12

**🎮 [Live interactive playground](https://jio32-af.github.io/HRVSTCollection/)** · **📈 [Flagship app: harvestiq.streamlit.app](https://harvestiq.streamlit.app)**

A curated collection of computational math, physics, AI, and quantitative finance projects built from scratch — no frameworks doing the thinking.

---

## Features

* **5 live browser demos** — wave physics, pathfinding, N-body gravity, compression, and Fourier drawing. No install, click and play.
* **3 Python flagships** — a tax-loss harvester on real market data, a TSP solver with a published 5-page benchmark paper, and an 8-layer neural net with hand-written backprop.
* **Real benchmarks** — 400-trial TSP study (0.000%–0.066% optimality gap), loss curves, and fail-case logs kept in-repo.

## Tech Stack

* Python (NumPy, SciPy, Numba, pandas, yfinance, Flask) for solvers and finance
* Vanilla HTML/CSS/JS (Canvas, SVG) for every browser demo — zero build step
* GitHub Pages for hosting, GitHub Actions for CI

## Installation & Quick Start

```bash
git clone https://github.com/jio32-af/HRVSTCollection.git
cd HRVSTCollection
pip install -r requirements.txt
```

Run the TSP benchmark checks:

```bash
cd HRVSTTsp
python3 TSP.py selftest
```

Run the neural-net letter mirror (opens in your browser):

```bash
cd ../HRVSTNeural
python3 demo.py
```

Or skip installs entirely — open the [live playground](https://jio32-af.github.io/HRVSTCollection/) and every demo runs in the page.

## Usage Examples

Solve a TSP correctness self-test from the CLI:

```bash
cd HRVSTTsp
python3 TSP.py selftest          # correctness + determinism checks
python3 TSP.py --sizes 100 1000  # scale series over given sizes
```

Drive the from-scratch neural net in three lines of Python:

```python
from network import HRVSTNet  # run with HRVSTNeural/ as the working dir

net = HRVSTNet([4, 8, 8, 16, 16, 8, 8, 4, 2])
pred = net.forward([0.5, -0.5, 0.25, 0.0])
loss, _ = net.train_step([0.5, -0.5, 0.25, 0.0], [0.0, 0.0], lr=0.01)
```

## Project Structure

```text
HRVSTCollection/
├── index.html            # landing page (GitHub Pages)
├── HRVSTWave/            # 2D FDTD wave simulator (browser)
├── HRVSTGraph/           # A* vs Dijkstra vs Greedy lab (browser)
├── HRVSTSim/             # Barnes–Hut N-body engine (browser)
├── HRVSTCompress/        # Huffman + LZW visualizer (browser)
├── HRVSTDraw/            # DFT epicycle studio (browser)
├── HRVSTTsp/             # Euclidean TSP solver + benchmark paper
│   ├── TSP.py
│   └── HRVST_TSP_Benchmark_Paper.pdf
├── HRVSTNeural/          # 8-layer MLP in pure Python
│   ├── network.py, layer.py, activations.py, loss.py
│   ├── train.py, demo.py
├── HrvstLab/             # tax-loss harvesting scanner (Streamlit)
├── requirements.txt
├── CONTRIBUTING.md
└── .github/workflows/ci.yml
```

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) — small PRs welcome. Keep demos dependency-free and new Python code typed where touched.

## License

MIT — see [LICENSE](./LICENSE).
