#!/usr/bin/env bash
# Single-call ADB device telemetry snapshot for Android game porting hill-climbs.
# Reads model, SoC, battery, RAM, process PID/RSS/Swap, multi-zone thermal sysfs
# (never trusting stale dumpsys thermalservice), and CPU cluster frequency caps.
# Read-only: never stops or touches a running game.
#
#   bash device_health.sh [--serial SERIAL] [--pkg PACKAGE] [--json]
set -euo pipefail

SERIAL=""
PKG=""
JSON=0

while [ $# -gt 0 ]; do
  case "$1" in
    --serial|-s) SERIAL="${2:?missing serial}"; shift 2 ;;
    --pkg|-p)    PKG="${2:?missing package}"; shift 2 ;;
    --json)      JSON=1; shift ;;
    -h|--help)
      sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

ADB_CMD=(adb)
if [ -n "$SERIAL" ]; then
  ADB_CMD+=(-s "$SERIAL")
fi

# Collect everything in a single adb shell round trip.
RAW="$("${ADB_CMD[@]}" shell "PKG='$PKG'"'
model=$(getprop ro.product.model 2>/dev/null | tr -d "\r")
soc=$(getprop ro.soc.model 2>/dev/null | tr -d "\r")
[ -z "$soc" ] && soc=$(getprop ro.board.platform 2>/dev/null | tr -d "\r")
android=$(getprop ro.build.version.release 2>/dev/null | tr -d "\r")
sdk=$(getprop ro.build.version.sdk 2>/dev/null | tr -d "\r")
echo "META|model=$model|soc=$soc|android=$android|sdk=$sdk"

awk "/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/{printf \"MEM|%s=%s\n\", \$1, \$2}" /proc/meminfo 2>/dev/null

batt=$(dumpsys battery 2>/dev/null | tr -d "\r")
blevel=$(printf "%s\n" "$batt" | awk "/level:/{print \$2; exit}")
btemp=$(printf "%s\n" "$batt" | awk "/temperature:/{print \$2; exit}")
bac=$(printf "%s\n" "$batt" | awk "/AC powered:|USB powered:|Wireless powered:/{if(\$NF==\"true\") on=1} END{print on+0}")
echo "BATT|level=${blevel:-}|temp_dc=${btemp:-}|powered=${bac:-0}"

if [ -n "$PKG" ]; then
  pid=$(pidof "$PKG" 2>/dev/null | awk "{print \$1}")
  if [ -n "$pid" ]; then
    rss=$(awk "/^VmRSS:/{print \$2}" /proc/"$pid"/status 2>/dev/null)
    swp=$(awk "/^VmSwap:/{print \$2}" /proc/"$pid"/status 2>/dev/null)
    echo "PROC|pkg=$PKG|pid=$pid|rss_kb=${rss:-0}|swap_kb=${swp:-0}"
  else
    echo "PROC|pkg=$PKG|pid=|rss_kb=0|swap_kb=0"
  fi
fi

for z in /sys/class/thermal/thermal_zone*; do
  [ -r "$z/temp" ] || continue
  t=$(cat "$z/temp" 2>/dev/null | tr -d "\r\n ")
  ty=$(cat "$z/type" 2>/dev/null | tr -d "\r\n")
  [ -n "$t" ] && echo "THERM|zone=$(basename "$z")|type=$ty|raw=$t"
done

for p in /sys/devices/system/cpu/cpufreq/policy*; do
  [ -d "$p" ] || continue
  cur=$(cat "$p/scaling_cur_freq" 2>/dev/null | tr -d "\r\n")
  smax=$(cat "$p/scaling_max_freq" 2>/dev/null | tr -d "\r\n")
  cmax=$(cat "$p/cpuinfo_max_freq" 2>/dev/null | tr -d "\r\n")
  echo "CPU|policy=$(basename "$p")|cur_khz=${cur:-0}|scaling_max_khz=${smax:-0}|cpuinfo_max_khz=${cmax:-0}"
done
' 2>&1)" || { echo "adb error: $RAW" >&2; exit 1; }

python3 - "$JSON" "$RAW" <<'PY'
import json, sys

as_json = sys.argv[1] == "1"
raw = sys.argv[2]

data = {
    "meta": {},
    "memory_mib": {},
    "battery": {},
    "process": None,
    "thermals_c": [],
    "temp_max_c": None,
    "cpu_policies": [],
    "cpu_policy_capped": False,
}

def parse_kv(parts):
    d = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            d[k] = v.strip()
    return d

for line in raw.splitlines():
    parts = line.strip().split("|")
    if not parts:
        continue
    tag = parts[0]
    kv = parse_kv(parts[1:])
    if tag == "META":
        data["meta"] = kv
    elif tag == "MEM":
        for k, v in kv.items():
            key = k.rstrip(":").lower()
            try:
                data["memory_mib"][key] = round(int(v) / 1024.0, 1)
            except ValueError:
                pass
    elif tag == "BATT":
        lvl = int(kv["level"]) if kv.get("level", "").isdigit() else None
        tdc = int(kv["temp_dc"]) if kv.get("temp_dc", "").lstrip("-").isdigit() else None
        data["battery"] = {
            "level_pct": lvl,
            "temp_c": round(tdc / 10.0, 1) if tdc is not None else None,
            "powered": kv.get("powered") == "1",
        }
    elif tag == "PROC":
        pid = int(kv["pid"]) if kv.get("pid", "").isdigit() else None
        data["process"] = {
            "package": kv.get("pkg"),
            "pid": pid,
            "running": pid is not None,
            "rss_mib": round(int(kv.get("rss_kb", "0") or 0) / 1024.0, 1),
            "swap_mib": round(int(kv.get("swap_kb", "0") or 0) / 1024.0, 1),
        }
    elif tag == "THERM":
        try:
            v = float(kv.get("raw", ""))
        except ValueError:
            continue
        c = v / 1000.0 if abs(v) > 200 else (v / 10.0 if abs(v) > 130 else v)
        if 15.0 <= c <= 125.0:
            data["thermals_c"].append({"zone": kv.get("zone"), "type": kv.get("type"), "temp_c": round(c, 1)})
    elif tag == "CPU":
        cur = int(kv.get("cur_khz", "0") or 0)
        smax = int(kv.get("scaling_max_khz", "0") or 0)
        cmax = int(kv.get("cpuinfo_max_khz", "0") or 0)
        capped = cmax > 0 and smax > 0 and smax < int(cmax * 0.95)
        if capped:
            data["cpu_policy_capped"] = True
        data["cpu_policies"].append({
            "policy": kv.get("policy"),
            "cur_mhz": round(cur / 1000.0, 0) if cur > 0 else None,
            "scaling_max_mhz": round(smax / 1000.0, 0) if smax > 0 else None,
            "cpuinfo_max_mhz": round(cmax / 1000.0, 0) if cmax > 0 else None,
            "capped": capped,
        })

data["thermals_c"].sort(key=lambda x: x["temp_c"], reverse=True)
if data["thermals_c"]:
    data["temp_max_c"] = data["thermals_c"][0]["temp_c"]
data["thermals_top5"] = data["thermals_c"][:5]
del data["thermals_c"]

if as_json:
    print(json.dumps(data, indent=2))
else:
    m = data["meta"]
    print(f"Device:   {m.get('model', '?')} (SoC: {m.get('soc', '?')}, Android {m.get('android', '?')} / SDK {m.get('sdk', '?')})")
    mem = data["memory_mib"]
    print(f"Memory:   {mem.get('memavailable', '?')} MiB available / {mem.get('memtotal', '?')} MiB total (SwapFree {mem.get('swapfree', '?')} MiB)")
    b = data["battery"]
    print(f"Battery:  {b.get('level_pct', '?')}% ({'AC/USB' if b.get('powered') else 'BATTERY'}), batt temp {b.get('temp_c', '?')} C")
    if data["process"] is not None:
        p = data["process"]
        state = f"RUNNING pid={p['pid']} RSS={p['rss_mib']} MiB Swap={p['swap_mib']} MiB" if p["running"] else "not running"
        print(f"Package:  {p['package']} — {state}")
    if data["thermals_top5"]:
        tops = ", ".join(f"{z['type']}={z['temp_c']}C" for z in data["thermals_top5"])
        print(f"Thermals: max {data['temp_max_c']} C ({tops})")
    if data["cpu_policies"]:
        def fmt_mhz(v):
            return str(int(v)) if v is not None else "?"
        pols = ", ".join(
            f"{c['policy']}={fmt_mhz(c['cur_mhz'])}/{fmt_mhz(c['scaling_max_mhz'])}/{fmt_mhz(c['cpuinfo_max_mhz'])}MHz"
            + (" [CAP]" if c["capped"] else "")
            for c in data["cpu_policies"]
        )
        print(f"CPU MHz:  {pols}")
PY
