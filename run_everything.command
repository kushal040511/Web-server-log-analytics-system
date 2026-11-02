#!/bin/bash
# Log Analytics System — run EVERYTHING: build, cluster, pipeline, bot detection, full ML stage, website.
# Double-click in Finder. Progress is also written to output/run_everything.log.
cd "$(dirname "$0")"
mkdir -p output
LOG=output/run_everything.log
exec > >(tee "$LOG") 2>&1
stage() { echo; echo "===== [$(date '+%H:%M:%S')] $*"; }
echo "== Project: $(pwd)"

command -v docker >/dev/null 2>&1 || { echo "ERROR: docker is not installed. Install Docker Desktop and run this again."; exit 1; }
command -v make   >/dev/null 2>&1 || { echo "ERROR: make is missing. Run: xcode-select --install"; exit 1; }
if ! docker info >/dev/null 2>&1; then
  echo "== Docker daemon not running — starting Docker Desktop ..."
  open -a Docker || { echo "ERROR: could not start Docker Desktop"; exit 1; }
  for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 3
    [ $i -eq 60 ] && { echo "ERROR: Docker did not start in 3 minutes"; exit 1; }; done
fi
echo "== $(docker --version)"
docker info --format 'Docker memory: {{.MemTotal}}' | awk '{printf "== Docker memory: %.1f GB (8 GB recommended)\n", $3/1073741824}'

set -e
stage "1/6 make build";  make build
stage "2/6 make up";     make up
stage "3/6 make all  (ingest -> ETL -> 11 reports -> Z-score anomalies -> bot detection -> dashboard)"; make all
stage "4/6 make ml   (attack data -> preprocessing -> tuning -> 8 models -> cross-dataset -> stability -> NASA audit, ~2 h)"; make ml
stage "5/6 make site (re-export and restart the website)"; make site
stage "6/6 status";      make status
echo; echo "===== [$(date '+%H:%M:%S')] ALL DONE"
echo "Website: http://localhost:8050   HDFS: http://localhost:9870   Spark: http://localhost:8080"
open http://localhost:8050 || true
