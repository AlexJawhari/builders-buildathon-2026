#!/bin/sh
# bench.sh DATA N: run N times, print min wall and min user+sys CPU (seconds). Env passes through.
D=${1:-../data/big20}; N=${2:-5}; BIN=${BIN:-target/release/shop}
for i in $(seq $N); do rm -rf /tmp/bench_out; mkdir /tmp/bench_out; /usr/bin/time -f "%e %U %S" env DATA_DIR=$D OUTPUT_DIR=/tmp/bench_out $BIN 2>&1 >/dev/null; done | awk 'NR==1||$1<w{w=$1} NR==1||$2+$3<c{c=$2+$3} END{printf "min wall %.2f  min cpu %.2f\n", w, c}'
