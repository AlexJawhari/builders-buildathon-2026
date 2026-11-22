#!/bin/bash
# usage: fetch_list.sh list.tsv destdir [jobs]   (list: path<TAB>bytes<TAB>urlpath); skips files already complete
BASE=https://machina-input.joinbuilders.org
DEST="$2"; JOBS="${3:-16}"
export BASE DEST
fetch_one() { p="$1"; b="$2"; u="$3"; f="$DEST/$p"
  [ -f "$f" ] && [ "$(stat -c %s "$f")" = "$b" ] && return 0
  mkdir -p "$(dirname "$f")"
  curl -fsS --retry 5 --retry-delay 2 -o "$f" "$BASE/$u" || echo "FAIL $p" >&2; }
export -f fetch_one
tr '\n' '\0' < "$1" | xargs -0 -n1 -P "$JOBS" bash -c 'IFS=$'"'"'\t'"'"' read -r p b u <<< "$1"; fetch_one "$p" "$b" "$u"' _
