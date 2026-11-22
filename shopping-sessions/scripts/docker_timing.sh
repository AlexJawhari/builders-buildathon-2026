#!/bin/sh
# docker_timing.sh DATA: like docker_run.sh but prints SHOP_TIMING phase lines (stderr). IMAGE/CPUS overridable.
IMAGE="${IMAGE:-shopping-sessions:latest}"; CPUS="${CPUS:-8}"
OUT=$(mktemp -d); chmod 777 $OUT
docker run --rm --platform linux/amd64 --network none --read-only --tmpfs /tmp:rw,size=64m,mode=1777 --cap-drop ALL --security-opt no-new-privileges --pids-limit 4096 --cpus $CPUS --memory 24g --memory-swap 24g --user 1000:1000 -v "$(cd $1 && pwd)":/data:ro,z -v $OUT:/output:z -e SHOP_TIMING=1 -e NCPU=$CPUS $IMAGE
docker run --rm --entrypoint /bin/true $IMAGE >/dev/null 2>&1
rm -rf $OUT 2>/dev/null
