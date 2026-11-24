#!/bin/bash
# Double-click to smoke-test BLACKHOLE GAME with real Chrome (outside the AI sandbox).
cd "$(dirname "$0")"
PORT=8766
python3 -m http.server $PORT >/dev/null 2>&1 &
SRV=$!
sleep 1
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless --no-sandbox --disable-gpu \
  --user-data-dir=/tmp/chrome-bh-smoke \
  --enable-logging=stderr --v=0 \
  --screenshot=/tmp/bh-smoke.png --window-size=1280,800 \
  --virtual-time-budget=8000 --hide-scrollbars \
  http://127.0.0.1:$PORT/index.html 2>/tmp/bh-console.log
echo "--- console errors/warnings ---"
grep -iE "error|uncaught|failed" /tmp/bh-console.log | head -20 || echo "(none)"
echo "--- screenshot: /tmp/bh-smoke.png ---"
kill $SRV 2>/dev/null
