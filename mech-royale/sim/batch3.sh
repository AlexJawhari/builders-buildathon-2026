#!/bin/bash
cd "$(dirname "$0")/.."
for p in "$@"; do
  for i in vision omni; do echo "$p $i $(MECH_P="$p" python3 sim/sim.py 150 4 $i 2>&1 | grep -E "mean score|avg cubes|dmg taken" | tr '\n' ' ')"; done
done
