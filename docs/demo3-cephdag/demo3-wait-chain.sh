#!/bin/sh
# Wait until the demo3-campaigns chain has exited. Exit 0 if it exited 0 and none of its
# processes remain, 2 if it exited non-zero and none remain, 1 if it was lost (no exit code
# and no process) or is still running at the start deadline. Prints a heartbeat at every poll.
set -eu

run=${CHAIN_RUN:-$HOME/runs/demo3-campaigns-20261113-081401}
pattern=${CHAIN_PATTERN:-'demo3-campaigns-20261113-081401/run\.sh|tools/demo3-chain\.sh|experiments/campaigns/demo3-(sweep|skew|droid|scale)\.json|docker exec cephdag8-n0 python3 -m experiments\.'}
poll=${POLL_SECONDS:-300}
deadline=${START_DEADLINE:-$(TZ=America/New_York date -d '2026-11-15 12:00' +%s)}
lost_after=3

say() {
    echo "$(TZ=America/New_York date '+%F %T %Z') $*"
}

running() {
    pgrep -f "$pattern" > /dev/null
}

say "waiting for $run to exit; polling every $poll s"
absent=0
while :; do
    if [ -f "$run/exit_code" ] && ! running; then
        code=$(cat "$run/exit_code")
        if [ "$code" = 0 ]; then
            say "the chain exited 0 and none of its processes remain"
            exit 0
        fi
        say "the chain exited $code and none of its processes remain"
        exit 2
    fi
    if running; then
        absent=0
    else
        absent=$((absent + 1))
    fi
    if [ "$absent" -ge "$lost_after" ]; then
        say "no exit code and no chain process in $lost_after polls: the chain was lost; stopping"
        exit 1
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
        say "the chain has not finished by the start deadline $(TZ=America/New_York date -d "@$deadline" '+%F %T %Z'); stopping"
        exit 1
    fi
    last=$(tail -n 1 "$run/output.log" 2> /dev/null || true)
    say "waiting: chain running=$([ "$absent" = 0 ] && echo yes || echo no); its last line: $last"
    sleep "$poll"
done
