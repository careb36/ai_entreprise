#!/usr/bin/env sh
# Start the Fabrique workspace daemon on this computer (macOS or Linux).
#
# The daemon is what lets the agents work in the folders where you cloned your
# GitHub/GitLab projects: it lists folders for the picker, checks a project,
# writes the agents' files and runs commands there. It connects to the NATS
# server of the Docker stack (nats://localhost:4222, override with NATS_URL)
# and keeps retrying until it is up.
#
# When the broker has one user per component (SECURITY.md, "Securing the
# bus"), give the daemon its credentials:
#   NATS_DAEMON_PASSWORD=...  ./scripts/start-daemon.sh   (user "daemon")
#   NATS_USER=... NATS_PASSWORD=...  ./scripts/start-daemon.sh
#   ./scripts/start-daemon.sh --creds ~/.fabrique/daemon.creds   (or NATS_CREDS_FILE)
#   NATS_NKEY_SEED=~/.fabrique/daemon.nk ./scripts/start-daemon.sh
# and, for TLS, NATS_TLS=true with NATS_TLS_CA=<the CA that signed the broker>.
set -eu

while [ $# -gt 0 ]; do
  case "$1" in
    --creds)
      [ $# -ge 2 ] || { echo "--creds needs a file" >&2; exit 1; }
      NATS_CREDS_FILE=$2; export NATS_CREDS_FILE; shift 2 ;;
    --creds=*)
      NATS_CREDS_FILE=${1#--creds=}; export NATS_CREDS_FILE; shift ;;
    -h|--help)
      sed -n '2,20p' "$0"; exit 0 ;;
    *)
      echo "unknown option: $1 (try --help)" >&2; exit 1 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DAEMON_DIR="$ROOT/vscode-daemon"
VENV="$DAEMON_DIR/.venv"
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3.9 or newer is required (or set PYTHON=/path/to/python3)." >&2
  exit 1
fi

if [ ! -x "$VENV/bin/python" ]; then
  if ! "$PYTHON" -m venv "$VENV"; then
    echo "Could not create a virtual environment. On Ubuntu/Debian: sudo apt install python3-venv" >&2
    exit 1
  fi
fi

# The hash-pinned lock (vscode-daemon/requirements.in): a package whose bytes
# differ from the recorded sha256 is refused. It includes nkeys, which signs
# the connection when a .creds file or an nkey seed is used.
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --require-hashes -r "$DAEMON_DIR/requirements.txt"

# The broker's "daemon" user, from the same variable docker compose reads.
if [ -z "${NATS_USER:-}" ] && [ -n "${NATS_DAEMON_PASSWORD:-}" ]; then
  NATS_USER=daemon
  NATS_PASSWORD=$NATS_DAEMON_PASSWORD
  export NATS_USER NATS_PASSWORD
fi
NATS_URL="${NATS_URL:-nats://localhost:4222}"
export NATS_URL
exec "$VENV/bin/python" "$DAEMON_DIR/daemon.py"
