#!/bin/sh
# Start nats-server for the stack (docker-compose.yml mounts this folder at
# /etc/fabrique-nats). JetStream keeps its data in /data.
#
# Authentication (issue #327): with NATS_BACKEND_PASSWORD and
# NATS_DAEMON_PASSWORD both set, the broker loads nats-server.conf, one user
# per component with only the subjects it needs (scripts/nats_config.py).
# With neither, it runs unauthenticated as before, which is safe only while
# NATS_BIND is 127.0.0.1. With only one, it refuses to start rather than
# leave a component locked out or the bus half open.
#
# TLS: when certs/server.crt and certs/server.key exist, clients must connect
# with TLS (NATS_TLS=true on the backend and the daemon); certs/ca.crt, when
# present, also verifies client certificates if NATS_TLS_VERIFY=true.
# docs/OPERATIONS.md, "Securing the bus", generates them.
set -eu

DIR=$(cd "$(dirname "$0")" && pwd)
CERTS=${NATS_CERTS_DIR:-$DIR/certs}
STORE=${NATS_STORE_DIR:-/data}
BACKEND=${NATS_BACKEND_PASSWORD:-}
DAEMON=${NATS_DAEMON_PASSWORD:-}

set -- -js -sd "$STORE" "$@"
if [ -n "$BACKEND" ] && [ -n "$DAEMON" ]; then
  set -- -c "$DIR/nats-server.conf" "$@"
  echo "nats: authentication on (users backend and daemon)"
elif [ -n "$BACKEND" ] || [ -n "$DAEMON" ]; then
  echo "nats: set both NATS_BACKEND_PASSWORD and NATS_DAEMON_PASSWORD, or neither" >&2
  exit 1
else
  echo "nats: no credentials; anything that reaches the broker can ask the daemon to run commands" >&2
fi

if [ -f "$CERTS/server.crt" ] && [ -f "$CERTS/server.key" ]; then
  set -- "$@" --tls --tlscert "$CERTS/server.crt" --tlskey "$CERTS/server.key"
  if [ -f "$CERTS/ca.crt" ]; then
    set -- "$@" --tlscacert "$CERTS/ca.crt"
    if [ "${NATS_TLS_VERIFY:-false}" = "true" ]; then
      set -- "$@" --tlsverify
    fi
  fi
  echo "nats: TLS on"
fi

exec "${NATS_SERVER_BIN:-nats-server}" "$@"
