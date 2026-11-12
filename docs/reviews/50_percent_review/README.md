# Log Analytics System — 50% Review

Core pipeline: NASA web logs → HDFS → Spark ETL → Spark SQL reports + anomaly detection → website.
Each person owns one big data tool.

```
NASA logs ──▶ HDFS raw ──▶ Spark ETL ──▶ HDFS curated (Parquet, by date)
   (Kriti)      (Kriti)     (Kushal)                │
                                                    ▼
                        Spark SQL: 11 reports + Z-score anomalies (Gargi)
                                                    │
                                                    ▼
                               dashboard.json + tables ──▶ website :8050
                                         (Gargi)              (Gargi)
```

| Person | Big data tool | Folder | Part they built |
|---|---|---|---|
| Kriti  | Hadoop HDFS (in Docker) | `Kriti/`  | Docker image and 7-container cluster, HDFS with 2 DataNodes and replication 2, NASA data download, Makefile |
| Kushal | Apache Spark | `Kushal/` | Spark ETL (`etl.py`): raw logs into clean, typed Parquet, split by date |
| Gargi  | Spark SQL | `Gargi/`  | 11 Spark SQL reports (`reports.py`), Z-score anomaly detection (`anomalies.py`), export and the 4-page website |

Each person's notes are in their folder: `KRITI.md`, `KUSHAL.md`, `GARGI.md`, and what to install is in `REQUIREMENTS_<NAME>.md`.

## Run it

The three folders fit together into one project. Copy them into one folder, then run it:

```bash
mkdir loganalytics-50 && cp -R Kriti/. Kushal/. Gargi/. loganalytics-50/
cd loganalytics-50
make data       # download the NASA-HTTP logs
make build      # build the Docker image (one time)
make up         # start HDFS + Spark
make skeleton   # ingest -> ETL -> reports -> anomalies -> dashboard
```

Then open http://localhost:8050 (website), http://localhost:9870 (HDFS), http://localhost:8080 (Spark).
Docker Desktop needs at least 8 GB of memory.

## Dataset note

NASA-HTTP, 1 July – 31 August 1995. Four days are missing from the source itself
(29, 30, 31 July and 2 August), so the charts show a gap there. One line in the July file is
truncated; the ETL keeps it aside as a reject.
