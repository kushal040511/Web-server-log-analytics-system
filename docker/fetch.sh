#!/usr/bin/env bash
# fetch <path under /dist/> <output file>: download an Apache release from the fastest mirror that has it.
set -u
path=$1 out=$2
for base in \
    https://dlcdn.apache.org \
    https://downloads.apache.org \
    https://repo.huaweicloud.com/apache \
    https://mirrors.ocf.berkeley.edu/apache \
    https://mirror.lyrahosting.com/apache \
    https://archive.apache.org/dist; do
  echo "trying $base/$path"
  if curl -fL --retry 2 --connect-timeout 15 --speed-limit 51200 --speed-time 20 "$base/$path" -o "$out"; then
    echo "downloaded from $base"
    exit 0
  fi
done
echo "every mirror failed for $path" >&2
exit 1
