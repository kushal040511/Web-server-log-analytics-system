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
	@echo "  Dashboards   http://localhost:8088   (Apache Superset, admin / admin)"
	@echo "  Spark SQL    jdbc:hive2://localhost:10000/weblogs   (Spark Thrift Server)"
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

bots:            ## run the GMM automated-traffic detector (Spark MLlib, unsupervised)
	$(EXEC) spark-submit /workspace/jobs/bot_detection.py

skeleton: ingest etl reports anomalies  ## the end-to-end thin slice: raw -> HDFS -> Spark ETL -> Spark SQL reports -> anomalies
	@echo
	@echo "=============================================="
	@echo " End-to-end pipeline complete."
	@echo " HDFS browser: http://localhost:9870"
	@echo " Spark master: http://localhost:8080"
	@echo " Dashboards:   make bi, then http://localhost:8088"
	@echo "=============================================="


all: skeleton bots bi  ## pipeline + bot detection + the BI dashboards

## ---------------------------------------------------------------- dashboards (Apache Superset)
# Superset reads the Parquet tables in HDFS through the Spark Thrift Server (Spark SQL over JDBC).

ml-publish:      ## turn the ML results (output/ml/*.json) into Spark tables in HDFS for the dashboards
	$(EXEC) spark-submit /workspace/jobs/publish_ml_tables.py

bi-tables:       ## register every HDFS table in the Spark Thrift Server (database weblogs)
	$(EXEC) beeline -u jdbc:hive2://thrift:10000 --force=true --silent=true \
	  --hivevar BASE=$(HDFS)/data/weblogs -f /workspace/sql/bi_tables.sql

dashboards:      ## (re)build the 5 Superset dashboards from superset/build_dashboards.py
	$(DC) exec -T superset python /app/loganalytics/build_dashboards.py \
	  http://localhost:8088 admin admin hive://thrift:10000/weblogs
	@echo
	@echo "  Dashboards: http://localhost:8088  (admin / admin)"

bi: ml-publish bi-tables dashboards  ## publish ML tables, register all tables, build the dashboards

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

.PHONY: help build up down destroy status logs shell data ingest etl reports anomalies bots skeleton all ml-publish bi-tables dashboards bi ls report sql fsck

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
	@echo "  Results: output/ml/ml_report.html; dashboards: make bi, then http://localhost:8088"

ml-cross:        ## cross-dataset test: train on one dataset, test on the other (~10 min)
	$(EXEC) spark-submit /workspace/jobs/ml_crossdataset.py

ml-tune:         ## check the model settings on a validation split carved from the training set (~15 min)
	$(EXEC) spark-submit /workspace/jobs/ml_tune.py

ml-robust:       ## repeat the top 3 models on 5 random splits: mean ± std, ROC/PR-AUC, McNemar (~25 min)
	$(EXEC) spark-submit /workspace/jobs/ml_robustness.py

ml-audit:        ## sort the NASA URLs the model flags into hand-checked categories
	$(EXEC) python3 /workspace/scripts/audit_nasa_flags.py /workspace/output/ml/ml_results.json

ml: ml-data ml-ingest ml-prep ml-tune ml-train ml-cross ml-robust ml-audit  ## the whole ML stage; then `make bi` for the dashboards

.PHONY: ml-data ml-ingest ml-prep ml-tune ml-train ml-cross ml-robust ml-audit ml

## ---------------------------------------------------------------- quality

test:            ## run the test suite without Docker (pip install -r requirements-dev.txt; needs Java 11)
	python3 -m pytest -v

bench:           ## scalability benchmark of the ETL and reports on 25-200% of the logs (~30 min, no Docker)
	python3 scripts/bench_scalability.py data output/bench

.PHONY: test bench
