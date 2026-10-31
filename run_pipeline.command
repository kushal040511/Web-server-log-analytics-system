#!/bin/bash
# Log Analytics System — one-shot runner (build image, start cluster, run pipeline)
cd "$(dirname "$0")"
echo "== Project: $(pwd)"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed. Install Docker Desktop (https://www.docker.com/products/docker-desktop/) and run this again."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "== Docker daemon not running — starting Docker Desktop ..."
  open -a Docker || { echo "ERROR: could not start Docker Desktop"; exit 1; }
  for i in $(seq 1 60); do
    docker info >/dev/null 2>&1 && break
    sleep 3
    [ $i -eq 60 ] && { echo "ERROR: Docker did not start in 3 minutes"; exit 1; }
  done
fi
echo "== Docker is up: $(docker --version)"

set -e
echo; echo "== make build (first run downloads ~1.1 GB of Apache tarballs)"; make build
echo; echo "== make up";       make up
echo; echo "== make skeleton"; make skeleton
echo
echo "== DONE. Website: http://localhost:8050   HDFS: http://localhost:9870   Spark: http://localhost:8080"
