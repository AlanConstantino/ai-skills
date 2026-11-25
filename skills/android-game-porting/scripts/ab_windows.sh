#!/usr/bin/env bash
# Hands-off same-run A/B: a scripted input loop plays the game while a live switch takes each value in turn for a fixed window;
# temperatures and the GPU clock are sampled every ~10 s; the log is pulled at the end and `probe.py windows` cuts it per value.
#
#   ab_windows.sh <SWITCH|setting:NAME> <seconds per window> <value> [<value>...]
#   ab_windows.sh --check-steps <steps file>        # validate an input loop without a device
#
# Environment:
#   PKG       package (required)          SERIAL  adb serial (optional)
#   FILES     app files dir on device     (default /sdcard/Android/data/$PKG/files)
#   CTL       live control file           (default $FILES/ctl.txt; nx2android debug builds read it, release builds do not)
#   STEPS     input loop file: one step a line, "<control line> <seconds to wait>", # comments (example below)
#   FORBID    buttons the loop must never press (default "A Plus Minus": confirm and menus advance story state and overwrite saves;
#             empty keeps the default, FORBID=none allows every button: only in a menu-free test scene)
#   RESTORE   value to put back at the end (strongly recommended: otherwise the last window's value stays live)
#   OUT       output folder (default .)
#   THERM_CPU / THERM_GPU  thermal zone type globs (default "cpu-*" / "gpu*")
#
# SWITCH is written as "env SWITCH=value" into CTL. setting:NAME runs "settings put system NAME value" (a device-wide setting, e.g. a
# handheld's performance mode) and writes "env PERF_NAME=value" into CTL only as a window marker (a non-DM_ name, so the host logs no
# unknown-switch warning). Example steps file (nx2android DM_CTL press syntax, "~" = hold seconds):
#   LUp+RRight~2.5 2.7     # run in a circle, camera turning
#   Y~0.2 0.5              # light attack
#   R~1.5 1.7              # hold fire
#   LDown+RLeft~2.5 2.7    # back the other way so the loop does not drift
set -u
FORBID=${FORBID:-"A Plus Minus"}

check_steps() {   # → 0 when every step's buttons avoid FORBID
    local f=$1 bad=0 n=0 line press b
    [ -f "$f" ] || { echo "no steps file: $f" >&2; return 2; }
    while IFS= read -r line || [ -n "$line" ]; do
        line=${line%%#*}; set -- $line; [ $# -ge 2 ] || continue
        press=${1%%~*}; n=$((n + 1))
        for b in ${press//+/ }; do
            for x in $FORBID; do [ "$b" = "$x" ] && { echo "step $n presses forbidden button $b: $line" >&2; bad=1; }; done
        done
        case $2 in ''|*[!0-9.]*) echo "step $n: wait '$2' is not seconds" >&2; bad=1 ;; esac
    done < "$f"
    [ $n -gt 0 ] || { echo "no steps in $f" >&2; return 2; }
    [ $bad = 0 ] && echo "steps ok: $n"
    return $bad
}

if [ "${1:-}" = "--check-steps" ]; then check_steps "${2:-}"; exit $?; fi
[ $# -ge 3 ] || { sed -n '2,8p' "$0"; exit 2; }
: "${PKG:?set PKG}"; : "${STEPS:?set STEPS (the input loop file)}"
check_steps "$STEPS" >/dev/null || exit 1
FILES=${FILES:-/sdcard/Android/data/$PKG/files}; CTL=${CTL:-$FILES/ctl.txt}; OUT=${OUT:-.}
THERM_CPU=${THERM_CPU:-cpu-*}; THERM_GPU=${THERM_GPU:-gpu*}
ADB=(adb); [ -n "${SERIAL:-}" ] && ADB=(adb -s "$SERIAL")
SW=$1; WIN=$2; shift 2
mkdir -p "$OUT"; TAG=$(date +%Y%m%d-%H%M%S); STOP="$OUT/.ab-stop-$TAG"; CSV="$OUT/therm-$TAG.csv"

pid=$("${ADB[@]}" shell pidof "$PKG" | tr -d '\r')
[ -n "$pid" ] || { echo "$PKG is not running: load the bench save first" >&2; exit 1; }

loop() {   # the input loop: stops on the stop file or when the game's PID changes
    while [ ! -e "$STOP" ]; do
        while IFS= read -r line || [ -n "$line" ]; do
            [ -e "$STOP" ] && return
            line=${line%%#*}; set -- $line; [ $# -ge 2 ] || continue
            [ "$("${ADB[@]}" shell pidof "$PKG" | tr -d '\r')" = "$pid" ] || { echo "PID changed: input loop stopped" >&2; touch "$STOP"; return; }
            "${ADB[@]}" shell "echo '$1' >> '$CTL'"; sleep "$2"
        done < "$STEPS"
    done
}
set_value() {
    case $SW in
        setting:*) "${ADB[@]}" shell "settings put system ${SW#setting:} $1; echo 'env PERF_${SW#setting:}=$1' >> '$CTL'" ;;
        *) "${ADB[@]}" shell "echo 'env $SW=$1' >> '$CTL'" ;;
    esac
}
sample() {
    "${ADB[@]}" shell "c=0; g=0; for z in /sys/class/thermal/thermal_zone*; do t=\$(cat \$z/type 2>/dev/null); v=\$(cat \$z/temp 2>/dev/null || echo 0);
        case \$t in $THERM_CPU) [ \$v -gt \$c ] && c=\$v;; $THERM_GPU) [ \$v -gt \$g ] && g=\$v;; esac; done;
        k=\$(cat /sys/class/kgsl/kgsl-3d0/gpuclk 2>/dev/null || echo 0); echo \$((c/1000)),\$((g/1000)),\$((k/1000000))" | tr -d '\r'
}

loop & LOOP=$!
trap 'touch "$STOP"; wait $LOOP 2>/dev/null' EXIT
echo "time,window,cpu_max_c,gpu_max_c,gpu_mhz" > "$CSV"
for v in "$@"; do
    set_value "$v"; echo "$(date +%T) $SW=$v"
    end=$((SECONDS + WIN))
    while [ $SECONDS -lt $end ] && [ ! -e "$STOP" ]; do echo "$(date +%T),$SW=$v,$(sample)" >> "$CSV"; sleep 9; done
done
touch "$STOP"; wait $LOOP 2>/dev/null
[ -n "${RESTORE:-}" ] && { set_value "$RESTORE"; echo "restored $SW=$RESTORE"; }
now=$("${ADB[@]}" shell pidof "$PKG" | tr -d '\r')
[ "$now" = "$pid" ] && echo "same PID $pid" || echo "PID CHANGED ($pid -> ${now:-none}): void the run, pull the log before relaunching"
"${ADB[@]}" pull "$FILES/log.txt" "$OUT/ab-$TAG.txt" >/dev/null && echo "log $OUT/ab-$TAG.txt, temperatures $CSV"
rm -f "$STOP"
echo "next: python3 $(dirname "$0")/probe.py windows $OUT/ab-$TAG.txt"
