#!/bin/sh
# Platforms like Railway mount volumes owned by root. Start as root, make /data
# writable for the unprivileged "vani" user, then drop privileges to run the app.
set -e
if [ "$(id -u)" = "0" ]; then
  mkdir -p /data/litellm_cache
  if chown -R vani:vani /data 2>/dev/null; then
    exec setpriv --reuid=vani --regid=vani --init-groups "$@"
  fi
  echo "vani: could not chown /data, continuing as root" >&2
fi
exec "$@"
