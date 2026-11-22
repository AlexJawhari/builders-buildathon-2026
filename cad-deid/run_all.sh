#!/bin/bash
# Full run: pass 1 (resumable) -> title-block pass for PDF/DXF rows scanned before it existed -> merge -> pass 2 into the store -> README.
# Detached usage: setsid nohup ./run_all.sh > ../work/run_all.log 2>&1 &
cd "$(dirname "$0")"
PY=../venv/bin/python; W=../work; M=../../_downloads/manifest.tsv
echo "[$(date)] pass1"
$PY -W ignore -m deid.pass1 $M $W/pass1.jsonl --workers 4 >> $W/pass1.log 2>&1
echo "[$(date)] pass1b (title blocks in PDF/DXF scanned before that code existed)"
$PY -W ignore -m deid.pass1 $M $W/pass1b.jsonl --workers 4 --only-from $W/pass1_old.jsonl >> $W/pass1b.log 2>&1
echo "[$(date)] merge"
$PY -W ignore -m deid.merge $M $W/idents.json $W/pass1.jsonl $W/pass1b.jsonl || exit 1
N=$($PY -c "import json;print(len(json.load(open('$W/idents.json'))))")
[ "$N" -gt 1500 ] || { echo "dictionary too small ($N), stopping"; exit 1; }
echo "[$(date)] pass2 with $N identities"
$PY -W ignore -m deid.pipeline $M $W/idents.json --workers 4 >> $W/pipeline.log 2>&1
echo "[$(date)] pass2 finished; writing README"
$PY -W ignore -m deid.readme $W/README.txt
echo "[$(date)] DONE"
