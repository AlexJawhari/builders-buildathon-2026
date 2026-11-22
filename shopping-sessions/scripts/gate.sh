#!/bin/sh
# gate.sh [BIN]: fixtures, dev1 vs the official answer, and reference crosschecks on several seeds. Prints one line each.
set -u
cd "$(dirname "$0")/.."
BIN=${1:-target/release/shop}; BIN=$(realpath "$BIN"); export BIN
echo "fixtures: $(python3 scripts/run_fixtures.py fixtures -- "$BIN" 2>&1 | tail -1)"
rm -rf /tmp/gate_dev1; mkdir /tmp/gate_dev1
DATA_DIR=../data/dev1_v5 OUTPUT_DIR=/tmp/gate_dev1 "$BIN"
echo "dev1: $(cd scripts && python3 compare.py /tmp/gate_dev1 ../../_downloads/v5/ans/dev-answer | tail -1)"
for spec in "11 300000 80000" "12 40000 9000" "13 5000 1500" "14 600000 150000" "15 1000 200" "16 100000 100000"; do
  set -- $spec
  echo "crosscheck $spec: $(sh scripts/crosscheck.sh $1 $2 $3 2>&1 | tail -1)"
done
