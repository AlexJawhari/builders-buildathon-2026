#!/bin/bash
# usage: sim/ab.sh '<json params>' [n]   -> mean score under both intel modes
cd "$(dirname "$0")/.."
for i in vision omni; do MECH_P="$1" python3 sim/sim.py ${2:-96} 4 $i 2>&1 | tail -1; done
