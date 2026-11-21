#!/bin/sh
# usage: evalrun.sh EVALDIR  -> runs the built image (blast-radius-entry) on EVALDIR like the scorer, prints score + container CPU
set -e
EV=$(cd "$1" && pwd)
OUT=$EV/output.jsonl
CID=$(docker create --platform linux/amd64 --network none --cpus 2 --memory 4096m --memory-swap 4096m --pids-limit 256 --read-only \
  --tmpfs /tmp:rw,size=64m,mode=1777 --cap-drop ALL --security-opt no-new-privileges --user 65534:65534 --env HOME=/tmp \
  -v $EV/commits:/data/commits:ro -v $EV/tests.json:/data/tests.json:ro blast-radius-entry)
START=$(date +%s.%N)
flock /home/alex/projects/buildathon/.heavy.lock sh -c "docker start -a $CID > $OUT 2> $EV/stderr.log"
END=$(date +%s.%N)
# CPU: docker keeps no cpu total after exit; use cgroup of the stopped container if still readable
echo "wall $(echo "$END - $START" | bc)"
docker rm $CID >/dev/null
python3 /home/alex/projects/buildathon/blast/score.py $EV $EV/answers.json $OUT
