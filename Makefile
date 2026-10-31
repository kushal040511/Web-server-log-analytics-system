# Log Analytics System for a Web Server
# Every step of the pipeline is one target. Run `make` to see them all.

SHELL := /bin/bash
DC    := docker compose
EXEC  := $(DC) exec -T client
HDFS  := hdfs://namenode:8020

.DEFAULT_GOAL := help

## ---------------------------------------------------------------- cluster

help:            ## show this help
	@echo "Log Analytics System — available targets"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	 | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "First run:  make data && make build && make up && make skeleton"

build:           ## build the image (first run downloads ~1.1 GB of Apache tarballs)
	$(DC) build

up:              ## start the cluster and wait for HDFS to accept writes
	$(DC) up -d
	@echo "waiting for HDFS to leave safe mode ..."
	@for i in $$(seq 1 60); do \
	   if $(EXEC) hdfs dfsadmin -safemode get 2>/dev/null | grep -q OFF; then \
	     echo "HDFS is ready"; break; \
	   fi; \
	   sleep 3; \
	   if [ $$i -eq 60 ]; then echo "HDFS did not come up — try: make logs"; exit 1; fi; \
	 done
	@$(MAKE) --no-print-directory status

down:            ## stop the cluster, keep the data
	$(DC) down

destroy:         ## stop the cluster and delete HDFS entirely
	$(DC) down -v

status:          ## show cluster health and the web UIs
	@echo
	@$(DC) ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || $(DC) ps
	@echo
	@echo "  Website      http://localhost:8050"
	@echo "  HDFS         http://localhost:9870"
	@echo "  Spark master http://localhost:8080"
	@echo "  Spark worker http://localhost:8081"
	@echo

logs:            ## tail the logs of every service
	$(DC) logs -f --tail=60

shell:           ## open a shell in the client container
	$(DC) exec client bash

## ---------------------------------------------------------------- pipeline

data:            ## download the NASA-HTTP logs into ./data
	@bash scripts/get_data.sh

ingest:          ## copy the raw logs from ./data into HDFS
	@echo "creating the HDFS layout ..."
	$(EXEC) hdfs dfs -mkdir -p /data/weblogs/raw /data/weblogs/curated /data/weblogs/reports /spark-logs
	@echo "uploading raw logs (this takes a minute) ..."
	$(EXEC) bash -c 'hdfs dfs -put -f /workspace/data/access_log_* /data/weblogs/raw/'
	@echo
	$(EXEC) hdfs dfs -du -h /data/weblogs/raw

etl:             ## parse the raw logs into the curated Parquet layer
	$(EXEC) spark-submit /workspace/jobs/etl.py

reports:         ## run the four core reports as Spark SQL
	$(EXEC) spark-submit /workspace/jobs/reports.py

anomalies:       ## Z-score anomaly detection on hourly and daily traffic (Spark SQL)
	$(EXEC) spark-submit /workspace/jobs/anomalies.py

dashboard:       ## export report tables to ./output for the website (JSON + CSV downloads)
	$(EXEC) spark-submit /workspace/jobs/export_dashboard.py
	@echo
	@echo "  Website ready → http://localhost:8050"

web:             ## (re)start just the website
	$(DC) up -d web
	@echo "  http://localhost:8050"

site:            ## rebuild the site's data and restart it
	@$(MAKE) --no-print-directory dashboard
	$(DC) restart web
	@echo "  http://localhost:8050"

skeleton: ingest etl reports anomalies dashboard  ## the end-to-end thin slice: raw -> HDFS -> Spark -> reports -> anomalies -> web
	@echo
	@echo "=============================================="
	@echo " End-to-end pipeline complete."
	@echo " Website:      http://localhost:8050"
	@echo " HDFS browser: http://localhost:9870"
	@echo " Spark master: http://localhost:8080"
	@echo "=============================================="


## ---------------------------------------------------------------- inspect

ls:              ## list what is in HDFS
	@echo "--- raw ---";      $(EXEC) hdfs dfs -du -h /data/weblogs/raw     || true
	@echo "--- curated ---";  $(EXEC) hdfs dfs -du -h /data/weblogs/curated || true
	@echo "--- reports ---";  $(EXEC) hdfs dfs -ls   /data/weblogs/reports  || true

report:          ## print one report table, e.g. make report T=top_urls N=10
	@$(EXEC) spark-sql --conf spark.ui.enabled=false \
	  -e "SELECT * FROM parquet.\`$(HDFS)/data/weblogs/reports/$(or $(T),top_urls)\` LIMIT $(or $(N),10)"

sql:             ## open an interactive Spark SQL prompt against the curated data
	$(DC) exec client spark-sql

fsck:            ## prove replication is real — every block should have 2 copies
	$(EXEC) hdfs fsck /data/weblogs/raw -files -blocks -locations | tail -25

.PHONY: help build up down destroy status logs shell data ingest etl reports anomalies dashboard web site skeleton ls report sql fsck

## ---------------------------------------------------------------- machine learning
# Web-attack detection: Factorization Machine vs 7 Spark MLlib models.
# Full run: make ml   (download -> HDFS -> preprocess -> train 8 models -> score NASA logs)

ml-data:         ## download the labelled attack datasets into ./data/attacks
	@bash scripts/get_attack_data.sh

ml-ingest:       ## copy the labelled attack data into HDFS (/data/attacks)
	$(EXEC) hdfs dfs -mkdir -p /data/attacks
	$(EXEC) bash -c 'hdfs dfs -put -f /workspace/data/attacks/*.csv /workspace/data/attacks/*.txt /data/attacks/'
	$(EXEC) hdfs dfs -du -h /data/attacks

ml-prep:         ## ML preprocessing: clean, bias fix, de-duplicate, 80/20 split
	$(EXEC) spark-submit /workspace/jobs/ml_preprocess.py

ml-train:        ## train FM + 7 models, confusion matrices, score the NASA logs (~30-40 min)
	$(EXEC) spark-submit /workspace/jobs/ml_train.py
	@echo
	@echo "  Results: http://localhost:8050/ml  (also output/ml/ml_report.html)"

ml-cross:        ## cross-dataset test: train on one dataset, test on the other (~10 min)
	$(EXEC) spark-submit /workspace/jobs/ml_crossdataset.py

ml: ml-data ml-ingest ml-prep ml-train ml-cross  ## the whole ML stage; results on http://localhost:8050/ml

.PHONY: ml-data ml-ingest ml-prep ml-train ml-cross ml
