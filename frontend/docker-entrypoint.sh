#!/bin/sh
# Write runtime config from the API_BASE_URL env var, so the same image works anywhere.
set -eu
target=/usr/share/nginx/html/config.js
if [ -n "${API_BASE_URL:-}" ]; then
  escaped=$(printf '%s' "$API_BASE_URL" | sed 's/[\\"]/\&/g')
  printf 'window.__VANI_CONFIG__ = { API_BASE_URL: "%s" };\n' "$escaped" > "$target"
else
  printf 'window.__VANI_CONFIG__ = {};\n' > "$target"
fi
echo "vani: config.js -> API_BASE_URL=${API_BASE_URL:-<build default>}"
