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
# with TLS (NATS_TLS=true on the backend and the daemon); with
# NATS_TLS_VERIFY=true, certs/ca.crt verifies their client certificates.
# NATS_TLS=true or NATS_TLS_VERIFY=true here make those files required: a
# missing one stops the broker rather than run without the TLS asked for.
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

truthy() { case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in 1|true|yes|on) return 0 ;; *) return 1 ;; esac; }
refuse() { echo "nats: $1; refusing to start without the TLS that was asked for" >&2; exit 1; }

# TLS asked for explicitly (NATS_TLS, NATS_TLS_VERIFY) must be what runs: a
# missing file stops the broker instead of starting it in the clear or
# without checking client certificates. Not asked for, TLS follows the files.
WANT_TLS=false; WANT_VERIFY=false
truthy "${NATS_TLS:-}" && WANT_TLS=true
truthy "${NATS_TLS_VERIFY:-}" && WANT_VERIFY=true && WANT_TLS=true
HAVE_CRT=false; HAVE_KEY=false
[ -f "$CERTS/server.crt" ] && HAVE_CRT=true
[ -f "$CERTS/server.key" ] && HAVE_KEY=true
if [ "$HAVE_CRT" != "$HAVE_KEY" ]; then
  refuse "$CERTS holds only one of server.crt and server.key"
fi
if [ "$WANT_TLS" = true ] && [ "$HAVE_CRT" = false ]; then
  refuse "TLS requested but $CERTS/server.crt and $CERTS/server.key are missing"
fi
if [ "$WANT_VERIFY" = true ] && [ ! -f "$CERTS/ca.crt" ]; then
  refuse "NATS_TLS_VERIFY=true but $CERTS/ca.crt is missing"
fi

if [ "$HAVE_CRT" = true ]; then
  set -- "$@" --tls --tlscert "$CERTS/server.crt" --tlskey "$CERTS/server.key"
  if [ -f "$CERTS/ca.crt" ]; then
    set -- "$@" --tlscacert "$CERTS/ca.crt"
  fi
  if [ "$WANT_VERIFY" = true ]; then
    set -- "$@" --tlsverify
    echo "nats: TLS on, client certificates required"
  else
    echo "nats: TLS on"
  fi
fi

exec "${NATS_SERVER_BIN:-nats-server}" "$@"
