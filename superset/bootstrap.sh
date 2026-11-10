#!/usr/bin/env bash
# Start Superset: create its metadata database and the admin user on the first run,
# then serve on port 8088.
set -e
ADMIN_PASSWORD="${SUPERSET_ADMIN_PASSWORD:-admin}"
SPARK_SQL_URI="${SPARK_SQL_URI:-hive://thrift:10000/weblogs}"

superset db upgrade
superset fab create-admin --username admin --firstname Log --lastname Analytics \
         --email admin@loganalytics.local --password "$ADMIN_PASSWORD" || true   # already exists on later runs
superset init

# The dashboards are built by `make bi` once the pipeline has written the tables to HDFS
# (superset/build_dashboards.py through the REST API of this server).

exec gunicorn --bind 0.0.0.0:8088 --workers 2 --worker-class gthread --threads 4 --timeout 300 \
     --limit-request-line 0 --limit-request-field_size 0 "superset.app:create_app()"
