#!/bin/bash
# Double-click to play BLACKHOLE GAME. Starts a local server and opens your browser.
cd "$(dirname "$0")"
PORT=8765
python3 -m http.server $PORT >/tmp/blackhole-game.log 2>&1 &
SRV=$!
sleep 1
open "http://localhost:$PORT/"
echo "BLACKHOLE GAME running at http://localhost:$PORT/ (PID $SRV)"
echo "Close this window to stop the server."
wait $SRV
