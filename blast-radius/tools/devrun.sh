#!/bin/sh
# run the working tree (not the built image) on an eval dir inside the dev image; usage: devrun.sh EVALDIR
EV=$(cd "$1" && pwd)
flock /home/alex/projects/buildathon/.heavy.lock docker run --rm --cpus 2 --memory 4g --user 65534:65534 -e HOME=/tmp \
  -e BLAST_DATA=/ev -e BLAST_SRC=/opt/sqlglot -e BLAST_MAP=/blast/data -e SKIP_INTEGRATION=0 --tmpfs /tmp:rw,size=64m,mode=1777 \
  -v /home/alex/projects/buildathon/blast:/blast:ro -v $EV:/ev:ro blast-dev python /blast/selector.py > $EV/output.jsonl 2> $EV/stderr.log
tail -3 $EV/stderr.log
python3 /home/alex/projects/buildathon/blast/score.py $EV $EV/answers.json $EV/output.jsonl | head -3
