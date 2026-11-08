#!/usr/bin/env bash
# Back up La Fabrique's Postgres database (and Neo4j when it is enabled), or
# restore a Postgres backup. Issue #328; the runbook is docs/OPERATIONS.md.
#
#   scripts/backup.sh                     back up now
#   scripts/backup.sh restore FILE --yes  restore FILE into $POSTGRES_DB
#
# Where Postgres is reached:
#   PG_CONTAINER  a running container to `docker exec` pg_dump in (e.g.
#                 factory-postgres with `docker compose`). Default: that
#                 container when it is running, else the pg_dump on PATH.
#   POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD,
#   POSTGRES_DB   as the backend reads them (defaults: localhost, 5432,
#                 factory, -, factory). Read from .env when unset.
#
# What is written, in BACKUP_DIR (default ./backups):
#   postgres-<db>-<UTC timestamp>.dump   pg_dump custom format, checked with
#                                        pg_restore --list once written
#   ….dump.sha256                        its checksum
#   neo4j-<UTC timestamp>.cypher         only with NEO4J_ENABLED=true and the
#                                        Neo4j container running (APOC export)
# Files of those names older than BACKUP_KEEP_DAYS (14) are removed; 0 keeps all.
#
# The audit hash chain lives in Postgres (audit_log), so a Postgres backup
# carries it whole; restore the whole database, never single tables, or
# GET /v1/audit/chain will report the rows that went missing.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

env_from_file() {
  # The value of $1 in .env, without sourcing the file (it may not be shell).
  local file="$REPO/.env"
  [ -f "$file" ] || return 0
  sed -n "s/^$1=//p" "$file" | tail -1 | sed 's/^"\(.*\)"$/\1/'
}

for name in POSTGRES_HOST POSTGRES_PORT POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB NEO4J_ENABLED NEO4J_PASSWORD; do
  if [ -z "${!name:-}" ]; then
    value="$(env_from_file "$name")"
    [ -n "$value" ] && export "$name=$value"
  fi
done

PG_HOST="${POSTGRES_HOST:-localhost}"
# The backend's .env names the compose service; from the host it is localhost.
[ "$PG_HOST" = "postgres" ] && PG_HOST=localhost
PG_PORT="${POSTGRES_PORT:-5432}"
PG_USER="${POSTGRES_USER:-factory}"
PG_DB="${POSTGRES_DB:-factory}"
BACKUP_DIR="${BACKUP_DIR:-$REPO/backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
NEO4J_CONTAINER="${NEO4J_CONTAINER:-factory-neo4j}"

container_running() {
  command -v docker >/dev/null 2>&1 &&
    [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)" = "true" ]
}

if [ -z "${PG_CONTAINER+set}" ]; then
  PG_CONTAINER=""
  container_running factory-postgres && PG_CONTAINER=factory-postgres
fi

# Run a Postgres client tool, in the container or on this machine.
pg() {
  local tool="$1"
  shift
  if [ -n "$PG_CONTAINER" ]; then
    docker exec -i -e PGPASSWORD="${POSTGRES_PASSWORD:-}" "$PG_CONTAINER" \
      "$tool" -U "$PG_USER" "$@"
  else
    PGPASSWORD="${POSTGRES_PASSWORD:-}" "$tool" -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" "$@"
  fi
}

restore() {
  local file="$1" confirm="${2:-}"
  [ -f "$file" ] || { echo "No such backup: $file" >&2; exit 2; }
  if [ -f "$file.sha256" ]; then
    (cd "$(dirname "$file")" && sha256sum -c "$(basename "$file").sha256" >/dev/null) ||
      { echo "Checksum mismatch for $file" >&2; exit 2; }
  fi
  if [ "$confirm" != "--yes" ]; then
    echo "This replaces the contents of database '$PG_DB'. Stop the backend and"
    echo "workers first, then run: $0 restore $file --yes"
    exit 2
  fi
  echo "Restoring $file into $PG_DB…"
  pg pg_restore --clean --if-exists --no-owner --exit-on-error -d "$PG_DB" <"$file"
  echo "Restored. Start the backend: it checks the schema is at head (alembic)."
}

backup() {
  mkdir -p "$BACKUP_DIR"
  local stamp out
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  out="$BACKUP_DIR/postgres-$PG_DB-$stamp.dump"
  echo "Backing up Postgres database '$PG_DB'${PG_CONTAINER:+ (container $PG_CONTAINER)}…"
  pg pg_dump -Fc -d "$PG_DB" >"$out.partial"
  # A dump pg_restore cannot list is no backup.
  pg pg_restore --list <"$out.partial" >/dev/null
  mv "$out.partial" "$out"
  (cd "$BACKUP_DIR" && sha256sum "$(basename "$out")" >"$(basename "$out").sha256")
  echo "Wrote $out ($(wc -c <"$out") bytes)"

  if [ "${NEO4J_ENABLED:-false}" = "true" ] && container_running "$NEO4J_CONTAINER"; then
    local graph="$BACKUP_DIR/neo4j-$stamp.cypher"
    echo "Exporting the Neo4j graph (APOC)…"
    docker exec -i "$NEO4J_CONTAINER" cypher-shell -u neo4j -p "${NEO4J_PASSWORD:-}" \
      --format plain \
      "CALL apoc.export.cypher.all(null, {stream: true, format: 'plain'}) YIELD cypherStatements RETURN cypherStatements" \
      >"$graph"
    echo "Wrote $graph"
  fi

  if [ "$KEEP_DAYS" -gt 0 ] 2>/dev/null; then
    find "$BACKUP_DIR" -maxdepth 1 -type f \
      \( -name 'postgres-*.dump' -o -name 'postgres-*.dump.sha256' -o -name 'neo4j-*.cypher' \) \
      -mtime +"$KEEP_DAYS" -print -delete
  fi
}

case "${1:-backup}" in
  backup) backup ;;
  restore)
    [ -n "${2:-}" ] || { echo "usage: $0 restore FILE --yes" >&2; exit 2; }
    restore "$2" "${3:-}"
    ;;
  -h | --help) sed -n '2,27p' "$0" ;;
  *) echo "usage: $0 [backup | restore FILE --yes]" >&2; exit 2 ;;
esac
