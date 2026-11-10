# Requirements — Kushal (Apache Spark ETL)

## Everyone needs this (to run the full project)

Java, Hadoop and Spark do **not** need to be installed on your laptop. They are downloaded
inside the Docker image the first time you run `make build`.

| Install | Version | Why | Check it works |
|---|---|---|---|
| Docker Desktop | 4.x or newer | Runs the whole cluster (HDFS + Spark + Superset dashboards) | `docker --version` |
| Docker memory | at least 8 GB | Docker Desktop → Settings → Resources → Memory (default 4 GB is too little) | `docker info \| grep "Total Memory"` |
| make | any | Runs the `make up`, `make etl` ... commands | `make --version` |
| curl + gunzip | any | Downloads and unzips the NASA logs | `curl --version` |
| Free disk | about 6 GB | Docker image (~1.1 GB of downloads) + logs (356 MB) + HDFS copies | |
| Internet | first run only | Image build and data download | |

**Mac:** install Docker Desktop from docker.com, then run `xcode-select --install` (gives `make`; `curl` and `gunzip` are already there).

**Windows (no new OS needed):**
1. Install **Docker Desktop**. It may ask to turn on WSL 2 — that is a Windows feature Docker uses in the background, not a new operating system. Say yes.
2. Install **Git for Windows** (git-scm.com). It comes with **Git Bash**, which already has `curl` and `gunzip`.
3. Install **make**: open PowerShell and run `winget install ezwinports.make`, then reopen Git Bash.
4. Run every project command in **Git Bash**.

**About the big first download:** `make build` downloads a small Linux image (Ubuntu 22.04 with Java) *inside Docker*. Hadoop and Spark need Linux, so they run inside it. It stays inside Docker and does not change your laptop's operating system.

**Linux:** install Docker Engine + the Compose plugin, then `sudo apt install -y make curl gzip`.

Optional: VS Code to read the code, Git to share changes.

## First run (all three folders copied into one folder)

```bash
make data && make build && make up && make skeleton
```
Then open http://localhost:8088 (dashboards, admin / admin), http://localhost:9870 (HDFS), http://localhost:8080 (Spark).

## Extra for your part (optional — to test your jobs without Docker)

Only needed if you want to run `etl.py` on your laptop directly.

| Install | Version | Check |
|---|---|---|
| Python | 3.10 or 3.11 | `python3 --version` |
| Java (JDK) | 11 (Eclipse Temurin, adoptium.net) | `java -version` |
| PySpark | 3.5.3 — `pip install -r requirements-kushal.txt` | `spark-submit --version` |

Run your jobs locally (from the combined project folder, after `make data`):

```bash
spark-submit --master "local[*]" jobs/etl.py ./data ./local/curated
```

