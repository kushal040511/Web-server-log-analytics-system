#!/usr/bin/env bash
# Download the labelled HTTP attack datasets into ./data/attacks/
#
#   HttpParams  payload_full.csv   31,067 parameter values: normal, sqli, xss, cmdi, path-traversal
#               https://github.com/Morzeux/HttpParamsDataset
#   Fwaf        goodqueries.txt    1,294,531 normal URLs
#               badqueries.txt        48,126 attack URLs
#               https://github.com/faizann24/Fwaf-Machine-Learning-driven-Web-Application-Firewall
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p data/attacks
cd data/attacks

HP="https://raw.githubusercontent.com/Morzeux/HttpParamsDataset/master"
FW="https://raw.githubusercontent.com/faizann24/Fwaf-Machine-Learning-driven-Web-Application-Firewall/master"

fetch() {                       # fetch <url> <local-name>
  if [ -f "$2" ]; then echo "  $2 already present — skipping"; return; fi
  echo "  downloading $2 ..."
  curl -fSL --retry 3 --connect-timeout 20 "$1" -o "$2.part"
  mv "$2.part" "$2"
}

echo "Fetching labelled attack data (about 29 MB)"
fetch "$HP/payload_full.csv" payload_full.csv
fetch "$FW/goodqueries.txt"  goodqueries.txt
fetch "$FW/badqueries.txt"   badqueries.txt
echo
wc -l payload_full.csv goodqueries.txt badqueries.txt
