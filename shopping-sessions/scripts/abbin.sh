#!/bin/sh
# abbin.sh N BIN_A BIN_B [DATA]: alternate two binaries, print per-run "wall user+sys" sorted, plus the minimum of each.
N=$1; A=$2; B=$3; D=${4:-../data/big20}
for i in $(seq $N); do
  for v in A B; do
    if [ $v = A ]; then X=$A; else X=$B; fi
    rm -rf /tmp/abb_out; mkdir /tmp/abb_out
    /usr/bin/time -f "$v %e %U %S" env DATA_DIR=$D OUTPUT_DIR=/tmp/abb_out $X 2>&1 >/dev/null
  done
done | awk '{w=$2; c=$3+$4; n[$1]++; if(!($1 in mw)||w<mw[$1])mw[$1]=w; if(!($1 in mc)||c<mc[$1])mc[$1]=c; sw[$1]+=w; sc[$1]+=c} END{for(v in n) printf "%s min wall %.2f min cpu %.2f | mean wall %.2f mean cpu %.2f\n", v, mw[v], mc[v], sw[v]/n[v], sc[v]/n[v]}'
