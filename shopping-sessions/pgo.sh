#!/bin/sh
# Profile-guided build: instrument, train on generated data, rebuild with the profile. Falls back to a plain build.
set -u
TGT=x86_64-unknown-linux-gnu
BASE="-C target-feature=+crt-static -C target-cpu=x86-64-v3"
plain() { RUSTFLAGS="$BASE" cargo build --release --locked --target $TGT && cp target/$TGT/release/shop /shop; }
LLVM=$(ls -d $(rustc --print sysroot)/lib/rustlib/$TGT/bin 2>/dev/null)
if [ -x "$LLVM/llvm-profdata" ] \
 && (cd /gen && cargo build --release --target-dir /gen-target) \
 && /gen-target/release/generate --seed 5 --events 3000000 --clicks 750000 --output /train >/dev/null \
 && RUSTFLAGS="$BASE -C profile-generate=/pgo" cargo build --release --locked --target $TGT --target-dir /pgo-target \
 && mkdir -p /train-out \
 && DATA_DIR=/train OUTPUT_DIR=/train-out NCPU=8 /pgo-target/$TGT/release/shop \
 && DATA_DIR=/train OUTPUT_DIR=/train-out NCPU=8 /pgo-target/$TGT/release/shop \
 && "$LLVM/llvm-profdata" merge -o /pgo/merged.profdata /pgo/*.profraw \
 && RUSTFLAGS="$BASE -C profile-use=/pgo/merged.profdata" cargo build --release --locked --target $TGT \
 && cp target/$TGT/release/shop /shop; then
  echo "pgo build ok"
else
  echo "pgo failed, plain build"
  plain || exit 1
fi
