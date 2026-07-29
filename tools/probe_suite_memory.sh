#!/usr/bin/env bash
# Attribute single-process suite memory growth to individual test modules.
#
# Runs the suite in ONE interpreter, in collection order, stopping after each
# prefix of N modules and recording peak RSS. A module that permanently retains
# memory shows up as a step change that never comes back down.
#
# Usage: tools/probe_suite_memory.sh [outfile]
set -u
OUT="${1:-/tmp/mem_probe.tsv}"
cd "$(dirname "$0")/.." || exit 1

mapfile -t MODS < <(ls tests/test_*.py)
printf 'modules_run\tlast_module\tpeak_rss_mb\trc\n' > "$OUT"

for n in 10 20 26 30 32 34 36 38; do
    [ "$n" -gt "${#MODS[@]}" ] && n=${#MODS[@]}
    subset=("${MODS[@]:0:$n}")
    pkill -9 -f kaleido >/dev/null 2>&1
    # Peak RSS via resource.getrusage in a wrapper — /usr/bin/time is not
    # installed in this image, and its absence silently returned rc=127 with a
    # 0 MB reading, which looks like "no growth" rather than "did not run".
    rm -f /tmp/.peak
    timeout 600 python3 -c '
import resource, sys, runpy
sys.argv = ["pytest", *sys.argv[1:]]
code = 0
try:
    runpy.run_module("pytest", run_name="__main__")
except SystemExit as e:
    code = e.code or 0
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
child = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
open("/tmp/.peak", "w").write(f"{peak}\t{child}")
sys.exit(code)
' "${subset[@]}" -q --timeout=120 --tb=no -p no:cacheprovider > /tmp/.probe.log 2>&1
    rc=$?
    peak=$(cut -f1 /tmp/.peak 2>/dev/null)
    printf '%s\t%s\t%s\t%s\n' "$n" "$(basename "${subset[-1]}")" \
        "$(( ${peak:-0} / 1024 ))" "$rc" >> "$OUT"
done
pkill -9 -f kaleido >/dev/null 2>&1
cat "$OUT"
echo done > "${OUT}.complete"
