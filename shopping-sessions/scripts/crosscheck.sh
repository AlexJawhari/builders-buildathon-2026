#!/bin/sh
# crosscheck.sh SEED EVENTS CLICKS: generate, run reference.py and the binary, compare. Needs `generate` at $GEN.
set -e
GEN=${GEN:-/home/alex/projects/buildathon/_downloads/v5/gen/generator/target/release/generate}
W=${WORK:-/tmp/xc}; mkdir -p $W; D=$W/d$1
rm -rf $D $W/ref$1 $W/out$1; $GEN --seed $1 --events $2 --clicks $3 --output $D >/dev/null
python3 "$(dirname $0)/reference.py" $D $W/ref$1
mkdir -p $W/out$1; DATA_DIR=$D OUTPUT_DIR=$W/out$1 "${BIN:-$(dirname $0)/../target/release/shop}"
(cd "$(dirname $0)" && python3 compare.py $W/out$1 $W/ref$1 | tail -1)
rm -rf $D
