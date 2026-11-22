#!/usr/bin/env python3
"""Group /tmp/shop_prof.txt samples by innermost frame in the shop crate (function and line). Usage: profsum2.py [binary]"""
import subprocess, collections, re, sys
binp = sys.argv[1] if len(sys.argv) > 1 else 'target/release/shop'
rows = [l.split() for l in open('/tmp/shop_prof.txt')]
out = subprocess.run(['addr2line', '-a', '-f', '-i', '-e', binp] + [r[0] for r in rows], capture_output=True, text=True).stdout.split('\n')
blocks = []; cur = None
for l in out:
    if l.startswith('0x'): cur = [l, []]; blocks.append(cur)
    elif cur is not None and l: cur[1].append(l)
tot = 0; fn = collections.Counter(); ln = collections.Counter()
for r, b in zip(rows, blocks):
    c = int(r[1]); tot += c
    fr = b[1]; pairs = [(fr[i], fr[i+1]) for i in range(0, len(fr) - 1, 2)]
    shop = [p for p in pairs if '/shop/src/' in p[1]]
    f, l = (shop[0] if shop else pairs[0])
    fn[re.sub(r'_R[A-Za-z]*Cs[0-9A-Za-z]+_\d+', '', f)[:50]] += c
    ln[l.split('/')[-1]] += c
print('total', tot)
for k, v in fn.most_common(18): print('%5.1f%% %s' % (100*v/tot, k))
print('---')
for k, v in ln.most_common(22): print('%5.1f%% %s' % (100*v/tot, k))
