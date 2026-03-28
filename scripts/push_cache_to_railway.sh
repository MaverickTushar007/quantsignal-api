#!/bin/bash
CACHE_FILE="$HOME/Desktop/quantsignal/data/signals_cache.json"
API="https://quantsignal-api-production.up.railway.app"
SECRET="quantsignal_cron_2026"

if [ ! -f "$CACHE_FILE" ]; then
  echo "❌ Cache file not found"
  exit 1
fi

COUNT=$(python3 -c "import json; d=json.load(open('$CACHE_FILE')); print(len(d))")

if [ "$COUNT" -eq "0" ]; then
  echo "❌ Cache is empty — aborting"
  exit 1
fi

echo "📦 Uploading $COUNT signals..."
curl -s -X POST "$API/api/v1/cron/upload-cache" \
  -H "X-Cron-Secret: $SECRET" \
  -H "Content-Type: application/json" \
  -d @"$CACHE_FILE" | python3 -m json.tool

echo "✅ Done"
