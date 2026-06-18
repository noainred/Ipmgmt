#!/usr/bin/env bash
# End-to-end demo with no nmap / root / real network required.
#
# 1. starts the portal on :8000
# 2. runs several "datacenter" collectors in --demo mode that push synthetic
#    host inventories to the portal
#
# Then open http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")/.."

export INGEST_API_KEY="${INGEST_API_KEY:-demo-key}"
export PORTAL_DB="${PORTAL_DB:-/tmp/ipmgmt-demo.db}"
export PORT="${PORT:-8000}"
rm -f "$PORTAL_DB" "$PORTAL_DB-wal" "$PORTAL_DB-shm"

echo "starting portal on :$PORT (db=$PORTAL_DB) ..."
python -m portal.app &
PORTAL_PID=$!
trap 'kill $PORTAL_PID 2>/dev/null || true' EXIT

# wait for the portal to answer
for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then break; fi
  sleep 0.3
done

push() { # id  name  location  subnets
  python -m collector.collector --demo --once \
    --portal "http://127.0.0.1:$PORT" --api-key "$INGEST_API_KEY" \
    --dc "$1" --dc-name "$2" --subnets "$4"
}

push dc-seoul-01   "Seoul DC 1"     "Seoul, KR"     "10.10.0.0/24,10.10.1.0/24"
push dc-busan-01   "Busan DC 1"     "Busan, KR"     "10.20.0.0/24"
push dc-tokyo-01   "Tokyo DC 1"     "Tokyo, JP"     "10.30.0.0/24,10.30.1.0/24"
push dc-virginia-01 "US-East DC 1"  "Virginia, US"  "172.16.0.0/24"

echo
echo "Demo ready -> http://127.0.0.1:$PORT"
echo "Press Ctrl-C to stop."
wait $PORTAL_PID
