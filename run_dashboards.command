#!/bin/bash
# Log Analytics System — start the cluster, the Superset dashboards and the LogLens app.
# Runs only the stages whose results are missing: a cluster that already holds the data
# goes straight to the dashboards (~10 min); an empty one runs the whole pipeline first.
# Double-click in Finder. Progress is also written to output/run_dashboards.log.
cd "$(dirname "$0")"
mkdir -p output
LOG=output/run_dashboards.log
exec > >(tee "$LOG") 2>&1
stage() { echo; echo "===== [$(date '+%H:%M:%S')] $*"; }
EXEC="docker compose exec -T client"

command -v docker >/dev/null 2>&1 || { echo "ERROR: docker is not installed."; exit 1; }
command -v make   >/dev/null 2>&1 || { echo "ERROR: make is missing. Run: xcode-select --install"; exit 1; }
if ! docker info >/dev/null 2>&1; then
  echo "== Starting Docker Desktop ..."
  open -a Docker
  for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 3; done
  docker info >/dev/null 2>&1 || { echo "ERROR: Docker did not start in 3 minutes"; exit 1; }
fi
echo "== $(docker --version)"

set -e
stage "1 make build (cluster image + Superset image)"; make build
stage "2 make up";                                     make up

if ! $EXEC hdfs dfs -test -e /data/weblogs/curated/_SUCCESS 2>/dev/null; then
  [ -f data/access_log_Jul95 ] || { stage "data: download the NASA logs"; make data; }
  stage "3 make skeleton (HDFS was empty: ingest -> ETL -> reports -> anomalies)"; make skeleton
else
  echo "== HDFS already holds the curated logs - skipping the pipeline"
  $EXEC hdfs dfs -test -e /data/weblogs/reports/anomaly_hours/_SUCCESS || { stage "3 reports + anomalies"; make reports anomalies; }
fi

stage "4 make bots (GMM bot detection + its dashboard tables)"; make bots

if [ ! -f output/ml/ml_results.json ]; then
  stage "5 make ml (no ML results yet, ~2 h)"; make ml
else
  echo "== ML results already in output/ml - skipping the ML stage"
  $EXEC hdfs dfs -test -e /models/fm_attack/metadata || { stage "5 make ml-model (save the attack model for the app, ~5 min)"; make ml-model; }
fi

stage "6 make bi (ML tables -> Spark Thrift Server -> Superset dashboards -> LogLens app)"
for i in $(seq 1 40); do (echo > /dev/tcp/localhost/10000) 2>/dev/null && break; sleep 3; done
for i in $(seq 1 40); do curl -sf http://localhost:8088/health >/dev/null && break; sleep 3; done
make bi
make status
echo; echo "===== [$(date '+%H:%M:%S')] ALL DONE"
echo "LogLens app: http://localhost:3000    Superset: http://localhost:8088 (admin / admin)"
open http://localhost:3000 || true
