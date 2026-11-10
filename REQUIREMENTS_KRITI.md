# Requirements — Kriti (cluster and HDFS) — Windows

You do **not** install Java, Hadoop or Spark on your laptop. Your Dockerfile installs Hadoop 3.3.6,
Spark 3.5.3 and Java 11 inside the Docker image. You only need Docker Desktop and Git for Windows.

## 1. Install (Windows)

Your laptop keeps Windows. Nothing here installs a new operating system.

| Install | Where | Check it works (PowerShell) |
|---|---|---|
| Docker Desktop | docker.com → Download for Windows. Keep "Use WSL 2" ticked, restart when asked. | `docker --version` |
| Git for Windows | git-scm.com. Gives **Git Bash** (has `curl` and `gunzip`) — only needed for `get_data.sh`. | `git --version` |

**Before installing:** Windows 10 (22H2) or Windows 11, about 6 GB free disk, and at least 12 GB RAM in the laptop.
If Docker says virtualization is off, turn on "Virtualization / SVM / VT-x" in the BIOS.

WSL 2 is a Windows feature Docker uses in the background — it is not a new OS.
The first `docker compose build` downloads a small Linux image (Ubuntu with Java) *inside Docker*, because Hadoop and Spark need Linux. It stays inside Docker.

## 2. Give Docker 8 GB of memory

On Windows this is not in Docker settings. Open Notepad, paste this, and save it as
`C:\Users\<your-name>\.wslconfig` (choose "All files" so it is not saved as .txt):

```
[wsl2]
memory=8GB
```
Then in PowerShell run `wsl --shutdown` and start Docker Desktop again.

## 3. Get the data

Either copy `access_log_Jul95` and `access_log_Aug95` from Kushal into the `data` folder,
or open **Git Bash** in the project folder and run `bash scripts/get_data.sh`.

Do not open and save the `.sh` files in Notepad — it can add Windows line endings.

## Commands — PowerShell instead of `make`

`make` is a Mac/Linux tool. On Windows, open PowerShell in the combined project folder
and type the command on the right — it does exactly what the `make` target does.

| make target | PowerShell command |
|---|---|
| `make build` | `docker compose build` |
| `make up` | `docker compose up -d` — then wait about a minute |
| check HDFS is ready | `docker compose exec -T client hdfs dfsadmin -safemode get` (should say OFF) |
| `make ingest` (1) | `docker compose exec -T client hdfs dfs -mkdir -p /data/weblogs/raw /data/weblogs/curated /data/weblogs/reports /spark-logs` |
| `make ingest` (2) | `docker compose exec -T client bash -c "hdfs dfs -put -f /workspace/data/access_log_* /data/weblogs/raw/"` |
| `make ls` | `docker compose exec -T client hdfs dfs -du -h /data/weblogs/raw` |
| `make fsck` | `docker compose exec -T client hdfs fsck /data/weblogs/raw -files -blocks -locations` |
| `make etl` | `docker compose exec -T client spark-submit /workspace/jobs/etl.py` |
| `make reports` | `docker compose exec -T client spark-submit /workspace/jobs/reports.py` |
| `make anomalies` | `docker compose exec -T client spark-submit /workspace/jobs/anomalies.py` |
| `make bi` | see REQUIREMENTS_GARGI.md (Superset dashboards) |
| `make report T=top_urls N=5` | ``docker compose exec -T client spark-sql -e 'SELECT * FROM parquet.`hdfs://namenode:8020/data/weblogs/reports/top_urls` LIMIT 5'`` |
| `make down` | `docker compose down` |

Full run, in order: build → up → (wait) → ingest 1 → ingest 2 → etl → reports → anomalies → dashboard.
Then open http://localhost:8088 (dashboards, admin / admin), http://localhost:9870 (HDFS), http://localhost:8080 (Spark).

## Your demo, in PowerShell

1. `docker compose up -d` — open http://localhost:9870 and show 2 live DataNodes.
2. The two ingest commands — the logs land in `/data/weblogs/raw`.
3. The fsck command — every block shows 2 replicas.
4. `docker compose stop datanode2`, then the `make report` command — data is still readable.
   Then `docker compose start datanode2`.

The `Makefile` is still yours to explain: each target is one of the commands above.
