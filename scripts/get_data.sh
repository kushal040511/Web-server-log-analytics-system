#!/usr/bin/env bash
# Download the NASA-HTTP access logs into ./data/
#
# The canonical home is the Internet Traffic Archive:
#   https://ita.ee.lbl.gov/html/contrib/NASA-HTTP.html
# That host is frequently unreachable, so we default to a GitHub mirror of the
# same two files and fall back to the original.
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p data
cd data

MIRROR="https://raw.githubusercontent.com/greymd/NASA-HTTP/main"
ORIGIN="https://ita.ee.lbl.gov/traces"

fetch() {                       # fetch <remote-name> <local-name>
  local remote=$1 local_name=$2
  if [ -f "$local_name" ]; then
    echo "  $local_name already present ($(du -h "$local_name" | cut -f1)) — skipping"
    return
  fi
  echo "  downloading $remote ..."
  if ! curl -fSL --retry 3 --connect-timeout 20 "$MIRROR/$remote" -o "$local_name.gz.part"; then
    echo "  mirror failed, trying the Internet Traffic Archive ..."
    curl -fSL --retry 3 --connect-timeout 20 "$ORIGIN/$remote" -o "$local_name.gz.part"
  fi
  mv "$local_name.gz.part" "$local_name.gz"
  echo "  extracting ..."
  gunzip -f "$local_name.gz"
  echo "  $local_name  $(wc -l < "$local_name" | tr -d ' ') lines"
}

echo "Fetching NASA-HTTP logs (about 37 MB compressed, 356 MB extracted)"
fetch "NASA_access_log_Jul95.gz" "access_log_Jul95"
fetch "NASA_access_log_Aug95.gz" "access_log_Aug95"

echo
echo "Done. Files in ./data:"
ls -lh access_log_* | awk '{print "  " $9 "  " $5}'
echo
echo "First record:"
head -1 access_log_Jul95
