#!/usr/bin/env bash
# Active GCP version.
# Check recent BTC/ETH/SOL depth data, disk, and active systemd units.

set -uo pipefail

DATA_ROOT="${DATA_ROOT:-/data}"
MAX_AGE_MIN="${MAX_AGE_MIN:-10}"
MIN_FREE_GB="${MIN_FREE_GB:-10}"
PING_URL="${HC_PING_URL:-}"

fail=0
msgs=()
found_any=0

shopt -s nullglob
for symdir in "$DATA_ROOT"/market=futures/symbol=*; do
    [ -d "$symdir/kind=depth" ] || continue
    found_any=1
    sym=$(basename "$symdir")
    newest=$(find "$symdir/kind=depth" -name '*.jsonl.gz' -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | head -1 | cut -d' ' -f2-)
    if [ -z "$newest" ]; then
        msgs+=("$sym: no depth file")
        fail=1
        continue
    fi
    age_min=$(( ( $(date +%s) - $(stat -c %Y "$newest") ) / 60 ))
    if [ "$age_min" -gt "$MAX_AGE_MIN" ]; then
        msgs+=("$sym: stale ${age_min}m")
        fail=1
    else
        msgs+=("$sym: OK ${age_min}m")
    fi
done

if [ "$found_any" -eq 0 ]; then
    msgs+=("no symbol directories")
    fail=1
fi

free_gb=$(df -BG --output=avail "$DATA_ROOT" | tail -1 | tr -dc '0-9')
if [ "${free_gb:-0}" -lt "$MIN_FREE_GB" ]; then
    msgs+=("disk free ${free_gb}GB < ${MIN_FREE_GB}GB")
    fail=1
else
    msgs+=("disk free ${free_gb}GB")
fi

LOBCAP_UNITS="${LOBCAP_UNITS:-lobcap@btcusdt.service}"
for unit in $LOBCAP_UNITS; do
    state=$(systemctl is-active "$unit" 2>/dev/null || true)
    if [ "$state" != "active" ]; then
        msgs+=("$unit: $state")
        fail=1
    fi
done

ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
if [ "$fail" -eq 0 ]; then
    echo "[$ts] OK | ${msgs[*]}"
    [ -n "$PING_URL" ] && curl -fsS -m 10 --retry 3 "$PING_URL" >/dev/null
else
    echo "[$ts] FAIL | ${msgs[*]}"
    [ -n "$PING_URL" ] && curl -fsS -m 10 --retry 3 \
        --data-raw "$(printf '%s\n' "${msgs[@]}")" "$PING_URL/fail" >/dev/null
fi

exit "$fail"
