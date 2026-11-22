#!/bin/bash
# Pass 2 only (dictionary already merged): rewrite into the store, then README.
cd "$(dirname "$0")"
PY=../venv/bin/python; W=../work; M=../../_downloads/manifest.tsv
echo "[$(date)] pass2 with $($PY -c "import json;print(len(json.load(open('$W/idents.json'))))") identities"
$PY -W ignore -m deid.pipeline $M $W/idents.json --workers 4 >> $W/pipeline.log 2>&1
echo "[$(date)] pass2 finished; writing README"
$PY -W ignore -m deid.readme $W/README.txt
echo "[$(date)] DONE"
