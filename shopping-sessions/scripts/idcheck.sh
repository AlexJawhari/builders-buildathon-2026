#!/bin/sh
# idcheck.sh BIN_OLD BIN_NEW [DATA]: run both on DATA (default big20_v5) and compare outputs with the official comparer.
D=${3:-../data/big20_v5}
rm -rf /tmp/id_a /tmp/id_b; mkdir /tmp/id_a /tmp/id_b
DATA_DIR=$D OUTPUT_DIR=/tmp/id_a "$1"; DATA_DIR=$D OUTPUT_DIR=/tmp/id_b "$2"
cd "$(dirname "$0")" && python3 compare.py /tmp/id_b /tmp/id_a | tail -1
