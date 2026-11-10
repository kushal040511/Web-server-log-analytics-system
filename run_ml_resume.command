#!/bin/bash
# Resume the ML stage after the pandas fix: rebuild the image (only the Python layer changes),
# restart the cluster (HDFS data is kept), then train -> cross-dataset -> stability -> audit -> dashboards.
cd "$(dirname "$0")"
LOG=output/run_everything.log
exec > >(tee -a "$LOG") 2>&1
stage() { echo; echo "===== [$(date '+%H:%M:%S')] $*"; }
docker info >/dev/null 2>&1 || { open -a Docker; for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 3; done; }
set -e
stage "R1 make build (adds pandas + pyarrow)"; make build
stage "R2 make up (HDFS data kept)";        make up
stage "R3 make ml-train (8 models, ~30-40 min)"; make ml-train
stage "R4 make ml-cross";  make ml-cross
stage "R5 make ml-robust"; make ml-robust
stage "R6 make ml-audit";  make ml-audit
stage "R7 make bi";        make bi
echo; echo "===== [$(date '+%H:%M:%S')] ALL DONE"
open http://localhost:8088/superset/dashboard/attacks/ || true
