#!/bin/bash
# Double-click this to open the screener. Your browser opens automatically.
cd "$(dirname "$0")" || exit 1

# Use the numeric address (127.0.0.1) rather than "localhost" — on some Macs
# "localhost" resolves to IPv6 while the server listens on IPv4, which causes a
# "Safari can't connect to the server" error.
URL="http://127.0.0.1:8770"

# Already running? Just open the page and stop.
if curl -s -o /dev/null "$URL" 2>/dev/null; then
  open "$URL"
  echo "Screener is already running — opened it in your browser."
  echo "You can close this window."
  exit 0
fi

# Find python. Terminal normally has it on PATH, but check the usual install
# locations too so this keeps working if PATH is unusual.
PY="$(command -v python3)"
if [ -z "$PY" ]; then
  for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    [ -x "$c" ] && PY="$c" && break
  done
fi
if [ -z "$PY" ]; then
  echo ""
  echo "Python 3 is not installed. Get it from https://www.python.org/downloads/, then try again."
  echo "Press any key to close…"; read -r -n 1
  exit 1
fi

# Refresh the numbers if they weren't already updated today.
if [ "$(date -r public/data.json +%Y-%m-%d 2>/dev/null)" != "$(date +%Y-%m-%d)" ]; then
  echo "Getting today's prices… (about 30 seconds)"
  if ! "$PY" build.py; then
    echo ""
    echo "Couldn't fetch new prices — showing the last saved numbers instead."
    sleep 2
  fi
fi

echo "Starting the screener…"
echo "(Your browser will open by itself in a few seconds.)"

"$PY" -m http.server 8770 --bind 127.0.0.1 --directory public >/dev/null 2>&1 &
SERVER_PID=$!

# Wait until it actually answers before opening the browser (up to ~30s).
for i in $(seq 1 60); do
  if curl -s -o /dev/null "$URL" 2>/dev/null; then
    open "$URL"
    break
  fi
  sleep 0.5
done

echo ""
echo "Screener is running. Leave this window open while you use it."
echo "To quit, just close this window."
echo "-----------------------------------------------------------------------"
echo "On your phone (same wifi): http://$(ipconfig getifaddr en0 2>/dev/null):8770"

# Keep this window tied to the engine; closing it (or Ctrl+C) stops the app.
wait "$SERVER_PID"
