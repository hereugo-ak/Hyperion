#!/usr/bin/env bash
# Shard the suite one test module per interpreter, then sum the counts.
#
# WHY (audit 4.3): a single-process full run is OOM-killed (rc=137) at ~93% on
# this 985MB host. The cause is not a leak in our code — one kaleido export
# reserves a ~277MB Chromium tree for the LIFE OF THE INTERPRETER, and
# ChartGenerator.release_renderer() can only return it between batches, not
# lower the interpreter's own high-water mark. Sharding makes process exit do
# the reclaiming, which is the only thing that fully releases it.
#
# This is a measurement harness for a constrained host, NOT a substitute for
# fixing test isolation: each shard is a clean interpreter, so it also proves
# no module depends on another module's residue.
#
# Usage: tools/run_suite_sharded.sh [outdir]
set -u

OUT="${1:-/tmp/shards}"
cd "$(dirname "$0")/.." || exit 1
mkdir -p "$OUT"
rm -f "$OUT"/*.log "$OUT"/summary.tsv

printf 'module\tpassed\tfailed\terrors\tskipped\trc\tsecs\n' > "$OUT/summary.tsv"

for f in tests/test_*.py; do
    name="$(basename "$f" .py)"
    pkill -9 -f kaleido >/dev/null 2>&1
    start=$(date +%s)
    timeout 300 python3 -m pytest "$f" -q --timeout=120 --timeout-method=thread \
        --tb=line -p no:cacheprovider > "$OUT/$name.log" 2>&1
    rc=$?
    secs=$(( $(date +%s) - start ))

    line="$(grep -oE '[0-9]+ (passed|failed|error|errors|skipped)' "$OUT/$name.log" | tr '\n' ' ')"
    get() { echo "$line" | grep -oE "[0-9]+ $1" | head -1 | grep -oE '^[0-9]+' || true; }
    p=$(get passed); fl=$(get failed); sk=$(get skipped)
    er=$(get errors); [ -z "$er" ] && er=$(get error)
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$name" "${p:-0}" "${fl:-0}" "${er:-0}" "${sk:-0}" "$rc" "$secs" >> "$OUT/summary.tsv"
done

pkill -9 -f kaleido >/dev/null 2>&1
awk -F'\t' 'NR>1{p+=$2;f+=$3;e+=$4;s+=$5;t+=$7; if($6!=0&&$6!=1)bad=bad" "$1}
END{printf "\nTOTAL passed=%d failed=%d errors=%d skipped=%d wall=%ds\n",p,f,e,s,t;
    if(bad!="")printf "ABNORMAL rc (124=timeout,137=OOM):%s\n",bad}' "$OUT/summary.tsv" \
    | tee "$OUT/TOTAL.txt"
echo "done" > "$OUT/.complete"
