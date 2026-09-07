#!/usr/bin/env bash
# healthcheck.sh — 수집기가 살아있는지 확인하고, 죽었으면 알림
#
# 수집기가 조용히 죽는 것이 가장 위험하다. 며칠 뒤에 알아차리면
# 그 기간 데이터는 영영 복구할 수 없다.
#
# 동작
#   1) 각 심볼의 최신 depth 파일이 N분 이내에 갱신됐는지 확인
#   2) 디스크 여유 확인
#   3) 전부 정상이면 Healthchecks.io로 ping (무료)
#      → ping이 끊기면 Healthchecks가 이메일/텔레그램으로 알려준다 (dead man's switch)
#
# crontab -e 에 추가:
#   */10 * * * * /home/ubuntu/lobcap/healthcheck.sh >> /home/ubuntu/lobcap/health.log 2>&1

set -uo pipefail

DATA_ROOT="${DATA_ROOT:-/data}"
MAX_AGE_MIN="${MAX_AGE_MIN:-5}"      # 이 시간 넘게 파일이 안 커지면 이상
MIN_FREE_GB="${MIN_FREE_GB:-10}"
PING_URL="${HC_PING_URL:-}"          # https://hc-ping.com/<uuid>

fail=0
msgs=()

# ── 1. 심볼별 최신 파일 신선도 ──────────────────────────────
shopt -s nullglob
found_any=0
for symdir in "$DATA_ROOT"/market=*/symbol=*; do
    [ -d "$symdir/kind=depth" ] || continue
    found_any=1
    sym=$(basename "$symdir")
    newest=$(find "$symdir/kind=depth" -name '*.jsonl.gz' -printf '%T@ %p\n' 2>/dev/null \
             | sort -rn | head -1 | cut -d' ' -f2-)
    if [ -z "$newest" ]; then
        msgs+=("$sym: depth 파일 없음")
        fail=1
        continue
    fi
    age_min=$(( ( $(date +%s) - $(stat -c %Y "$newest") ) / 60 ))
    if [ "$age_min" -gt "$MAX_AGE_MIN" ]; then
        msgs+=("$sym: ${age_min}분간 갱신 없음 ($(basename "$newest"))")
        fail=1
    else
        msgs+=("$sym: OK (${age_min}분 전)")
    fi
done

if [ "$found_any" -eq 0 ]; then
    msgs+=("데이터 디렉터리에 심볼이 하나도 없음")
    fail=1
fi

# ── 2. 디스크 여유 ──────────────────────────────────────────
free_gb=$(df -BG --output=avail "$DATA_ROOT" | tail -1 | tr -dc '0-9')
if [ "${free_gb:-0}" -lt "$MIN_FREE_GB" ]; then
    msgs+=("디스크 여유 ${free_gb}GB — 임계치 ${MIN_FREE_GB}GB 미만")
    fail=1
else
    msgs+=("디스크 여유 ${free_gb}GB")
fi

# ── 3. systemd 유닛 상태 ────────────────────────────────────
for unit in $(systemctl list-units --plain --no-legend 'lobcap@*' 2>/dev/null | awk '{print $1}'); do
    state=$(systemctl is-active "$unit" 2>/dev/null)
    if [ "$state" != "active" ]; then
        msgs+=("$unit: $state")
        fail=1
    fi
done

# ── 결과 ────────────────────────────────────────────────────
ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
if [ "$fail" -eq 0 ]; then
    echo "[$ts] OK | ${msgs[*]}"
    [ -n "$PING_URL" ] && curl -fsS -m 10 --retry 3 "$PING_URL" > /dev/null
else
    echo "[$ts] FAIL | ${msgs[*]}"
    # /fail 로 ping하면 Healthchecks가 즉시 알림을 보낸다
    [ -n "$PING_URL" ] && curl -fsS -m 10 --retry 3 \
        --data-raw "$(printf '%s\n' "${msgs[@]}")" "$PING_URL/fail" > /dev/null
fi

exit "$fail"
