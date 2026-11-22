#!/bin/sh
# profsum.sh: resolve /tmp/shop_prof.txt (from SHOP_PROF=1) to source lines and print the top ones.
BIN=${BIN:-target/release/shop}
python3 - "$BIN" <<'PY'
import subprocess, sys, collections
binp = sys.argv[1]
# PIE base: assume non-PIE or compute from first sample; use addr2line with raw addresses (static glibc non-PIE) 
rows = [l.split() for l in open('/tmp/shop_prof.txt')]
addrs = [r[0] for r in rows]
out = subprocess.run(['addr2line','-f','-i','-e',binp] + addrs, capture_output=True, text=True).stdout.split('\n')
# addr2line -i prints multiple frames per addr; use -a to delimit
out = subprocess.run(['addr2line','-a','-f','-e',binp] + addrs, capture_output=True, text=True).stdout.split('\n')
cnt = collections.Counter(); fn = collections.Counter(); tot = 0
i = 0
for r in rows:
    # each addr: 0xADDR, func, file:line
    func, loc = out[i+1], out[i+2]; i += 3
    c = int(r[1]); tot += c
    cnt[loc.split('/')[-1]] += c; fn[func[:70]] += c
print('samples', tot)
for k, v in fn.most_common(14): print('%5.1f%% %s' % (100*v/tot, k))
print('--- lines')
for k, v in cnt.most_common(25): print('%5.1f%% %s' % (100*v/tot, k))
PY
