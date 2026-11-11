#!/usr/bin/env bash
# Run PostgreSQL 16, Redis 7 and RabbitMQ 3.12 as plain user processes on their default local ports, from the
# toolchain built by toolchain.sh. Development/test data only (credentials acc/acc_dev, loopback only).
#   infra.sh up | down | status | broker-stop | broker-start | pg-stop | pg-start
set -u
TC=${ACC_TOOLCHAIN:-$HOME/.local/acc-toolchain}
RUN=$TC/run
PGBIN=$TC/pg/node_modules/@embedded-postgres/linux-x64/native/bin
PGDATA=$RUN/pgdata
ERLBIN=$TC/erlang/bin
RMQ=$TC/rabbitmq
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
mkdir -p "$RUN"

up_port() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

pg_start() {
  [ -x "$PGBIN/postgres" ] || { echo "postgres: not built (toolchain.sh services)"; return 1; }
  if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo acc_dev > "$RUN/pgpass.tmp"
    "$PGBIN/initdb" -D "$PGDATA" -U acc --pwfile="$RUN/pgpass.tmp" -A scram-sha-256 -E UTF8 --locale=C >/dev/null
    rm -f "$RUN/pgpass.tmp"
    cat >> "$PGDATA/postgresql.conf" <<CONF
listen_addresses = '127.0.0.1'
port = 5432
unix_socket_directories = '$RUN'
max_connections = 200
fsync = off
synchronous_commit = off
full_page_writes = off
CONF
  fi
  "$PGBIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1 || \
    "$PGBIN/pg_ctl" -D "$PGDATA" -l "$RUN/postgres.log" -w -t 60 start >/dev/null
  # the embedded package ships no psql: create the databases through the backend's own driver
  "$REPO/backend/.venv/bin/python" - <<'PY' || echo "postgres: could not create databases (backend/.venv missing?)"
import psycopg
with psycopg.connect("postgresql://acc:acc_dev@127.0.0.1:5432/postgres", autocommit=True) as c:
    for db in ("acc", "acc_test", "acc_e2e"):
        if not c.execute("select 1 from pg_database where datname = %s", (db,)).fetchone():
            c.execute(f'create database "{db}"')
PY
}
pg_stop() { "$PGBIN/pg_ctl" -D "$PGDATA" -m fast -w stop >/dev/null 2>&1 || true; }

redis_start() {
  [ -x "$TC/redis/bin/redis-server" ] || { echo "redis: not built (toolchain.sh services)"; return 1; }
  "$TC/redis/bin/redis-cli" -p 6379 ping >/dev/null 2>&1 || \
    "$TC/redis/bin/redis-server" --daemonize yes --port 6379 --bind 127.0.0.1 --save "" --appendonly no \
      --dir "$RUN" --logfile "$RUN/redis.log" >/dev/null
}
redis_stop() { "$TC/redis/bin/redis-cli" -p 6379 shutdown nosave >/dev/null 2>&1 || true; }

broker_start() {
  [ -x "$ERLBIN/erl" ] && [ -d "$RMQ/plugins" ] || { echo "rabbitmq: not built (toolchain.sh broker)"; return 1; }
  up_port 5672 && return 0
  mkdir -p "$RUN/rabbit/mnesia" "$RUN/rabbit/log" "$RUN/rabbit/conf"
  # No rabbitmqctl in this layout: users, vhosts and permissions are loaded from a definitions file at boot.
  cat > "$RUN/rabbit/conf/definitions.json" <<'DEFS'
{"users": [{"name": "acc", "password": "acc_dev", "tags": ["administrator"]}],
 "vhosts": [{"name": "/"}, {"name": "acc_test"}, {"name": "acc_e2e"}],
 "permissions": [{"user": "acc", "vhost": "/", "configure": ".*", "write": ".*", "read": ".*"},
                 {"user": "acc", "vhost": "acc_test", "configure": ".*", "write": ".*", "read": ".*"},
                 {"user": "acc", "vhost": "acc_e2e", "configure": ".*", "write": ".*", "read": ".*"}]}
DEFS
  cat > "$RUN/rabbit/conf/rabbitmq.conf" <<CONF
listeners.tcp.default = 127.0.0.1:5672
loopback_users = none
definitions.import_backend = local_filesystem
definitions.local.path = $RUN/rabbit/conf/definitions.json
CONF
  echo '[].' > "$RUN/rabbit/conf/enabled_plugins"
  # exec + redirection of the whole subshell: nothing keeps the caller's stdout open, so `infra.sh up | tee` returns
  (
    cd "$RUN/rabbit" || exit 1
    export PATH="$ERLBIN:$PATH" HOME="$RUN/rabbit" RABBITMQ_NODENAME=rabbit@localhost \
      RABBITMQ_MNESIA_BASE="$RUN/rabbit/mnesia" RABBITMQ_LOG_BASE="$RUN/rabbit/log" \
      RABBITMQ_CONFIG_FILE="$RUN/rabbit/conf/rabbitmq.conf" \
      RABBITMQ_ENABLED_PLUGINS_FILE="$RUN/rabbit/conf/enabled_plugins" RABBITMQ_PID_FILE="$RUN/rabbit/rabbit.pid" \
      ERL_EPMD_ADDRESS=127.0.0.1 RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS="-kernel inet_dist_use_interface {127,0,0,1}"
    exec setsid "$RMQ/sbin/rabbitmq-server"
  ) > "$RUN/rabbit/log/console.log" 2>&1 < /dev/null &
  for _ in $(seq 1 90); do up_port 5672 && return 0; sleep 1; done
  echo "rabbitmq: did not open port 5672 (see $RUN/rabbit/log/console.log)"; return 1
}
broker_stop() {
  # Stop exactly the node this script started: its pid file, then whatever is left of its own session.
  # (No `pkill -f <name>`: that also matches any unrelated command line that merely mentions the node.)
  local pid sid
  pid=$(cat "$RUN/rabbit/rabbit.pid" 2>/dev/null || true)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    sid=$(ps -o sid= -p "$pid" 2>/dev/null | tr -d ' ')
    kill "$pid" 2>/dev/null
    for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    [ -n "$sid" ] && pkill -s "$sid" 2>/dev/null
  fi
  for _ in $(seq 1 30); do up_port 5672 || return 0; sleep 1; done
  echo "rabbitmq: port 5672 is still open (another broker?)"; return 1
}

status() {
  if "$PGBIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; then echo "postgres: up"; else echo "postgres: down"; fi
  if "$TC/redis/bin/redis-cli" -p 6379 ping >/dev/null 2>&1; then echo "redis:    up"; else echo "redis:    down"; fi
  if up_port 5672; then echo "rabbitmq: up"; else echo "rabbitmq: down"; fi
}

case "${1:-}" in
  up) pg_start; redis_start; broker_start; status ;;
  down) broker_stop; redis_stop; pg_stop; status ;;
  status) status ;;
  broker-stop) broker_stop ;; broker-start) broker_start ;;
  pg-stop) pg_stop ;; pg-start) pg_start ;;
  *) sed -n '2,4p' "$0"; exit 2 ;;
esac
