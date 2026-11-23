#!/bin/bash
# Sourced only by the snap's hooks and launchers.
set -euo pipefail

read_config() {
    DATABASE_MODE="$(snapctl get database.mode)"
    DATABASE_MODE="${DATABASE_MODE:-bundled}"
    DATABASE_URL="$(snapctl get database.url)"
    PORT="$(snapctl get port)"
    PORT="${PORT:-8090}"
    JF_PORT="$(snapctl get jellyfin-port)"
    JF_PORT="${JF_PORT:-8096}"
    ABS_PORT="$(snapctl get audiobookshelf-port)"
    ABS_PORT="${ABS_PORT:-13378}"
    REDIS_URL="$(snapctl get redis.url)"
    export DATABASE_MODE DATABASE_URL PORT JF_PORT ABS_PORT REDIS_URL
}

validate_config() {
    case "$DATABASE_MODE" in
        bundled) ;;
        external)
            case "$DATABASE_URL" in
                postgres://?*|postgresql://?*) ;;
                *) echo 'database.mode=external requires a PostgreSQL database.url' >&2; return 1 ;;
            esac ;;
        *) echo 'database.mode must be bundled or external' >&2; return 1 ;;
    esac
    local port
    for port in "$PORT" "$JF_PORT" "$ABS_PORT"; do
        if [[ ! "$port" =~ ^[1-9][0-9]{0,4}$ ]] || (( port < 1024 || port > 65535 )); then
            echo 'Listener ports must be integers between 1024 and 65535' >&2
            return 1
        fi
    done
    if [[ "$PORT" == "$JF_PORT" || "$PORT" == "$ABS_PORT" || "$JF_PORT" == "$ABS_PORT" ]]; then
        echo 'The three listener ports must be distinct' >&2
        return 1
    fi
    case "$REDIS_URL" in
        ''|redis://?*|rediss://?*) ;;
        *) echo 'redis.url must be a redis:// or rediss:// URL' >&2; return 1 ;;
    esac
}

database_environment() {
    export PGHOST="$SNAP_COMMON/run/postgresql" PGPORT=5432 PGUSER=silo
    export PGCONNECT_TIMEOUT=5
    if [[ "$DATABASE_MODE" == bundled ]]; then
        DATABASE_URL="postgresql://silo@/silo?host=$PGHOST&sslmode=disable"
        export DATABASE_URL
    fi
}

drop_privileges() {
    exec "$SNAP/usr/bin/setpriv" --clear-groups --reuid=_daemon_ --regid=_daemon_ -- "$@"
}
