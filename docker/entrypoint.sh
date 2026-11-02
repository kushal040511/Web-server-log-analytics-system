#!/usr/bin/env bash
# One image, several roles. The role is the first argument (or $ROLE).
set -euo pipefail

ROLE="${1:-${ROLE:-client}}"
NN_DIR=/hadoop/dfs/name

log() { echo "[entrypoint] $*"; }

wait_for() {   # wait_for host port label
  local host=$1 port=$2 label=$3 tries=0
  until (echo > /dev/tcp/"$host"/"$port") 2>/dev/null; do
    tries=$((tries + 1))
    if [ $tries -gt 90 ]; then log "gave up waiting for $label ($host:$port)"; exit 1; fi
    [ $((tries % 10)) -eq 1 ] && log "waiting for $label at $host:$port ..."
    sleep 2
  done
  log "$label is up"
}

case "$ROLE" in

  namenode)
    if [ ! -d "$NN_DIR/current" ]; then
      log "no filesystem image found — formatting HDFS (first run only)"
      hdfs namenode -format -force -nonInteractive
    else
      log "existing filesystem image found — skipping format"
    fi
    log "starting NameNode (web UI on 9870, RPC on 8020)"
    exec hdfs namenode
    ;;

  datanode)
    wait_for namenode 8020 "NameNode RPC"
    log "starting DataNode"
    exec hdfs datanode
    ;;

  spark-master)
    log "starting Spark master (web UI on 8080, RPC on 7077)"
    exec "$SPARK_HOME"/bin/spark-class org.apache.spark.deploy.master.Master \
         --host spark-master --port 7077 --webui-port 8080
    ;;

  spark-worker)
    wait_for spark-master 7077 "Spark master"
    log "starting Spark worker"
    exec "$SPARK_HOME"/bin/spark-class org.apache.spark.deploy.worker.Worker \
         spark://spark-master:7077 --webui-port 8081
    ;;

  history)
    wait_for namenode 8020 "NameNode RPC"
    # the event-log directory has to exist before the server will start
    hdfs dfs -mkdir -p /spark-logs || true
    log "starting Spark history server (web UI on 18080)"
    exec "$SPARK_HOME"/bin/spark-class org.apache.spark.deploy.history.HistoryServer
    ;;

  web)
    # No wait_for here on purpose: the report pages only need ./output, so the
    # site must come up even when the cluster is down. The /sql page connects
    # lazily and reports its own error if the cluster is missing.
    export PYTHONPATH="$SPARK_HOME/python:$SPARK_HOME/python/lib/py4j-0.10.9.7-src.zip:${PYTHONPATH:-}"
    log "starting the website on 8050"
    exec python3 /workspace/web/app.py
    ;;

  client)
    wait_for namenode 8020 "NameNode RPC"
    log "client container ready — run: docker compose exec client bash"
    exec sleep infinity
    ;;

  *)
    # anything else is treated as a command to run
    exec "$@"
    ;;
esac
