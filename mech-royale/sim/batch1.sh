#!/bin/bash
cd "$(dirname "$0")/.."
for p in '{}' '{"retreat_hp":0.3}' '{"retreat_hp":0.7}' '{"outnum":3}' '{"outnum":9}' '{"lo":30,"hi":70}' '{"lo":80,"hi":150}' '{"home_frac":0.15}' '{"home_frac":0.9}' '{"scan":0.0}'; do
  echo "== $p"; MECH_P="$p" sim/ab.sh "$p" 150
done
