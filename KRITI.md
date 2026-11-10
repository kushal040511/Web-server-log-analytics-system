# Kriti — Hadoop HDFS: Docker cluster and storage

My big data tool is **Hadoop HDFS**. I built the platform everything runs on: one Docker image, a Hadoop + Spark cluster in
Docker Compose, and HDFS storage where every block is kept twice.

| My file | What it does |
|---|---|
| `docker/Dockerfile` | One image: JDK 11, Hadoop 3.3.6, Spark 3.5.3, numpy, pandas, pyarrow |
| `docker/entrypoint.sh` | Starts each container in its role: namenode, datanode, spark-master, spark-worker, client, thrift (Spark Thrift Server) |
| `docker-compose.yml` | The cluster: 1 NameNode, 2 DataNodes, Spark master + worker, client, Spark Thrift Server, Apache Superset |
| `conf/core-site.xml` | Points every service at `hdfs://namenode:8020` |
| `conf/hdfs-site.xml` | Replication factor 2, storage folders, permissions off |
| `scripts/get_data.sh` | Downloads the NASA-HTTP July and August 1995 logs (about 356 MB) |
| `Makefile` | One command per step: `build`, `up`, `ingest`, `ls`, `fsck` |

## Demo (about 3 minutes)

1. `make up` — open http://localhost:9870 and show 2 live DataNodes.
2. `make ingest` — the two raw log files land in `/data/weblogs/raw`.
3. `make fsck` — every block shows 2 replicas on 2 different DataNodes.
4. `docker compose stop datanode2`, then `make report T=top_urls N=5` — data is still readable.
   Then `docker compose start datanode2`.

## What to say

- The raw zone is written once and never edited, so it is the audit trail.
- Replication 2 means losing one DataNode loses no data — step 4 proves it.
- The architecture is truly distributed; the hardware is one laptop with 8 GB given to Docker.

## Likely questions

- Why two DataNodes? — The minimum needed to show replication working.
- Is this real big data on one laptop? — Real HDFS and Spark, running on one machine.

Hand over: "The data is now safe in HDFS. Kushal will show how Spark cleans it."
