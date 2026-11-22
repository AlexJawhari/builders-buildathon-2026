#!/bin/sh
# ab.sh N "ENV_A" "ENV_B" [DATA]: alternate runs of two env settings, print the 5 fastest wall times of each (seconds).
N=$1; A=$2; B=$3; D=${4:-../data/big20}; BIN=${BIN:-target/release/shop}
for i in $(seq $N); do
  for v in A B; do
    if [ $v = A ]; then E="$A"; else E="$B"; fi
    rm -rf /tmp/ab_out; mkdir /tmp/ab_out
    /usr/bin/time -f "$v %e" env $E DATA_DIR=$D OUTPUT_DIR=/tmp/ab_out $BIN 2>&1 >/dev/null
  done
done | sort -k1,1 -k2,2n | awk '{c[$1]++; if (c[$1]<=5) s[$1]=s[$1] " " $2} END{print "A:" s["A"]; print "B:" s["B"]}'
