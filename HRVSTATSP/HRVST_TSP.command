#!/bin/bash
# Double-click this file (or run it) to benchmark the TSP solver:
# 1,000 fuzz cases vs the exact Held-Karp optimum. Results land in failures/.
cd "$(dirname "$0")"
python3 -c "import numpy, scipy, matplotlib" 2>/dev/null || pip3 install numpy scipy matplotlib
python3 TSP.py --sizes 100 1000 10000 100000 1000000
