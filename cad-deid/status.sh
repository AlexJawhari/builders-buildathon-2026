#!/bin/bash
# One-screen status of the detached full run.
W=/home/alex/projects/buildathon/machina/work
echo "== stages"; tail -n 3 $W/run_all.log
echo "== pass1: $(wc -l < $W/pass1.jsonl) rows of 188722;  $(tail -n 1 $W/pass1.log)"
[ -f $W/pipeline.log ] && echo "== pipeline: $(grep -v -i mupdf $W/pipeline.log | grep -v '^$' | tail -n 1)"
if [ -f $W/state.db ]; then
  /home/alex/projects/buildathon/machina/venv/bin/python - <<'P'
import sqlite3
c=sqlite3.connect('/home/alex/projects/buildathon/machina/work/state.db')
print('== files by status:', c.execute("select status,count(*),round(sum(size)/1e9,1) from files group by 1").fetchall())
print('== uploaded files:', c.execute("select count(*) from files where up=1").fetchone()[0], ' local store GB:', round(c.execute("select coalesce(sum(size),0) from blobs where local=1").fetchone()[0]/1e9,1))
P
fi
echo "== upload: $(tail -n 1 $W/upload.log)"; df -h /home | tail -1 | awk '{print "disk free",$4}'
