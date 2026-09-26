#!/bin/bash
# Turn on visitor counting.
#
#   bash tools/enable_analytics.sh <token>     paste the Cloudflare token
#   bash tools/enable_analytics.sh --off       stop counting
#   bash tools/enable_analytics.sh --status    what is it doing now
#
# The token is public by design: it appears in the page source either way.
set -u
cd "$(dirname "$0")/.." || exit 1
F=docs/analytics-token.txt

case "${1:-}" in
  --status|"")
    T=$(tr -d '[:space:]' < "$F" 2>/dev/null)
    if [ -n "$T" ]; then
      echo "Counting is ON  (token ${T:0:6}…${T: -4})"
      echo "  dashboard: https://dash.cloudflare.com/?to=/:account/web-analytics"
    else
      echo "Counting is OFF — nothing is being recorded."
      echo
      echo "To turn it on:"
      echo "  1. https://dash.cloudflare.com/?to=/:account/web-analytics"
      echo "  2. Add a site  ->  tophercook7-maker.github.io"
      echo "  3. It shows a snippet containing  token: \"abc123…\"  — copy just that token"
      echo "  4. bash tools/enable_analytics.sh <token>"
    fi
    exit 0 ;;
  --off)
    : > "$F"
    echo "Counting turned off."
    ;;
  *)
    TOKEN=$(echo "$1" | tr -d '[:space:]"' )
    if ! echo "$TOKEN" | grep -Eq '^[0-9a-fA-F]{20,40}$'; then
      echo "That does not look like a Cloudflare Web Analytics token."
      echo "  got: '$TOKEN'"
      echo "  expected: 20-40 hex characters, the value of token: in the snippet"
      echo "  (copy the token only, not the whole <script> tag)"
      exit 1
    fi
    printf '%s\n' "$TOKEN" > "$F"
    echo "Token saved."
    ;;
esac

git add "$F" >/dev/null 2>&1
if git diff --cached --quiet -- "$F"; then
  echo "No change to publish."
  exit 0
fi
git commit -qm "Analytics: $([ -s "$F" ] && echo 'on' || echo 'off')" \
  -m "Cloudflare Web Analytics. No cookie, no consent banner, never sees who a visitor is." \
  -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
git pull --rebase --autostash -q origin main && git push -q origin main && echo "Published. Give it a few minutes, then reload the site once and check the dashboard."
