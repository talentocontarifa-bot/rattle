#!/usr/bin/env bash
# Sends a plain-text alert to Rattle's creator via Telegram. Never fails the job.
# Usage: bash scripts/telegram_alert.sh "message"
TEXT="${1:-Rattle alert}"
if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
  echo "Telegram not configured; alert skipped: $TEXT"
  exit 0
fi
curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
  --data-urlencode "text=${TEXT}" \
  --data-urlencode "disable_web_page_preview=true" > /dev/null || true
echo "Alert sent."
exit 0
