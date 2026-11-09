#!/bin/bash
# Minimal equivalent of the official postgres image entrypoint.
set -euo pipefail
mkdir -p "$PGDATA" /run/postgresql && chown -R postgres:postgres "$PGDATA" /run/postgresql
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "$POSTGRES_PASSWORD" > /tmp/pw && chown postgres /tmp/pw
  su postgres -c "/usr/lib/postgresql/16/bin/initdb -D $PGDATA -U $POSTGRES_USER --pwfile=/tmp/pw --auth=scram-sha-256 --encoding=UTF8 --locale=C.UTF-8 >/dev/null"
  rm /tmp/pw
  echo "host all all 0.0.0.0/0 scram-sha-256" >> "$PGDATA/pg_hba.conf"
  su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D $PGDATA -o '-c listen_addresses=localhost' -w start >/dev/null"
  PGPASSWORD="$POSTGRES_PASSWORD" psql -h localhost -U "$POSTGRES_USER" -d postgres -qc "CREATE DATABASE \"$POSTGRES_DB\""
  su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D $PGDATA -w stop >/dev/null"
fi
exec su postgres -c "/usr/lib/postgresql/16/bin/postgres -D $PGDATA -c listen_addresses='*'"
