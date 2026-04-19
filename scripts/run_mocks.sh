#!/usr/bin/env bash
# Start workflow + storage mock services with PID tracking, readiness probes,
# and fail-fast cleanup. Body of `make run-mocks`.
#
# Guarantees:
#   - Both mocks listen on their ports before control returns to the user
#     (or the script exits non-zero with a clear message).
#   - If either mock exits before the user sends SIGINT/SIGTERM, the sibling
#     is killed and the script exits 1.
#   - On Ctrl-C (SIGINT) or SIGTERM, both children are terminated and the
#     script exits 0.
#
# Tunables (env vars):
#   WORKFLOW_PORT         default 9000
#   STORAGE_PORT          default 9100
#   READINESS_TIMEOUT_S   default 10
set -u

WORKFLOW_PORT="${WORKFLOW_PORT:-9000}"
STORAGE_PORT="${STORAGE_PORT:-9100}"
READINESS_TIMEOUT_S="${READINESS_TIMEOUT_S:-10}"

wf_pid=""
st_pid=""
stopping=0

# Distinguishes a live process from a zombie; `kill -0` returns success on
# both, but for mock-death detection we want only live. macOS bash is 3.2
# and lacks `wait -n`, so we poll process state via `ps`.
is_live() {
    local pid=$1
    kill -0 "$pid" 2>/dev/null || return 1
    local state
    state=$(ps -o state= -p "$pid" 2>/dev/null | tr -d ' ')
    [[ -n "$state" && "$state" != Z* ]]
}

port_is_open() {
    local port=$1
    (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null
}

require_port_free() {
    local port=$1
    local name=$2
    if port_is_open "$port"; then
        echo "ERROR: ${name} port 127.0.0.1:${port} is already in use" >&2
        exit 1
    fi
}

kill_children() {
    if [[ -n "$wf_pid" ]]; then
        kill "$wf_pid" 2>/dev/null || true
    fi
    if [[ -n "$st_pid" ]]; then
        kill "$st_pid" 2>/dev/null || true
    fi
    # Give them 5s to exit cleanly, then SIGKILL anything still alive.
    local i=0
    while (( i < 50 )); do
        local alive=0
        [[ -n "$wf_pid" ]] && is_live "$wf_pid" && alive=1
        [[ -n "$st_pid" ]] && is_live "$st_pid" && alive=1
        (( alive == 0 )) && return 0
        sleep 0.1
        i=$(( i + 1 ))
    done
    [[ -n "$wf_pid" ]] && kill -9 "$wf_pid" 2>/dev/null || true
    [[ -n "$st_pid" ]] && kill -9 "$st_pid" 2>/dev/null || true
}

on_signal() {
    stopping=1
}

trap on_signal INT TERM
# EXIT runs on every exit path (normal, error, signal-after-flag). It is
# the last-line backstop that makes sure no child escapes this script.
trap kill_children EXIT

wait_ready() {
    local port=$1
    local pid=$2
    local name=$3
    local iterations=$(( READINESS_TIMEOUT_S * 10 ))
    local i=0
    while (( i < iterations )); do
        (( stopping == 1 )) && return 1
        if port_is_open "$port"; then
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "ERROR: ${name} mock exited before binding 127.0.0.1:${port}" >&2
            return 1
        fi
        sleep 0.1
        i=$(( i + 1 ))
    done
    echo "ERROR: ${name} mock did not bind 127.0.0.1:${port} within ${READINESS_TIMEOUT_S}s" >&2
    return 1
}

require_port_free "$WORKFLOW_PORT" workflow
require_port_free "$STORAGE_PORT" storage

echo "Starting mocks - workflow :${WORKFLOW_PORT}, storage :${STORAGE_PORT} (Ctrl-C to stop)"

uv run python -m mock_services.workflow_api &
wf_pid=$!

uv run python -m mock_services.storage_api &
st_pid=$!

if ! wait_ready "$WORKFLOW_PORT" "$wf_pid" workflow; then
    (( stopping == 1 )) && exit 0
    exit 1
fi
if ! wait_ready "$STORAGE_PORT" "$st_pid" storage; then
    (( stopping == 1 )) && exit 0
    exit 1
fi

echo "Both mocks ready. Press Ctrl-C to stop."

# Poll both pids. Exit the moment a signal flag is set or either child dies.
# Short sleep so Ctrl-C is responsive. `is_live` treats zombies as dead.
while (( stopping == 0 )); do
    if ! is_live "$wf_pid" || ! is_live "$st_pid"; then
        break
    fi
    sleep 0.5
done

if (( stopping == 1 )); then
    exit 0
fi

echo "ERROR: a mock exited unexpectedly; stopping the stack." >&2
exit 1
