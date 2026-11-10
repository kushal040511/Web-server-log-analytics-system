# Log Analytics System for a Web Server

A distributed **Hadoop HDFS + Apache Spark** pipeline that turns raw web-server access logs into
reports, unusual-traffic alerts, automated-traffic (bot) detection and web-attack detection. Demonstrated on the real NASA-HTTP logs
(July–August 1995, 3.46 million requests).

**Team:** Kushal Mohan (RA2411028010082) · Kriti Singh (RA2411028010081) · Gargi Singh (RA2411028010102)
**Guide:** Dr. Senthil Kumar K · SRM Institute of Science and Technology · Big Data Essentials

![System architecture](docs/system_architecture.png)

## Pipeline

| Stage | Big data tool | Owner | Code | Result |
|---|---|---|---|---|
| Store | **Hadoop HDFS** in Docker (1 NameNode, 2 DataNodes, replication 2) | Kriti | `docker/`, `docker-compose.yml`, `conf/` | 356 MB raw logs, every block stored twice |
| Clean | **Apache Spark** (PySpark ETL) | Kushal | `jobs/etl.py` | 3,461,613 lines → 3,461,612 rows, 1 reject, 58 date folders of Parquet (41.5 MB) |
| Analyse | **Spark SQL** | Gargi | `jobs/reports.py`, `jobs/anomalies.py` | 11 reports; Z-score finds 12 unusual hours (13 July 1995, STS-70 launch) |
| Detect bots | **Spark MLlib** (unsupervised) | Team | `jobs/bot_detection.py` | Gaussian Mixture Model on 9 per-host behaviour features; 1,829 of 38,104 hosts (4.8%, 6.4% of requests) flagged as automated, checked against hosts that fetched `/robots.txt` |
| Detect attacks | **Spark MLlib** | Team | `jobs/ml_preprocess.py`, `jobs/ml_train.py`, `jobs/ml_crossdataset.py` | Factorization Machine, F1 0.967, compared with 7 models (see [ML.md](ML.md)) |
| Visualise | **Apache Superset** on the **Spark Thrift Server** | Gargi | `superset/`, `sql/bi_tables.sql`, `jobs/publish_ml_tables.py` | 6 dashboards, 56 charts; every chart is a live Spark SQL query on the tables in HDFS |
| Use it | **LogLens** app with embedded Superset dashboards | Team | `app/`, `jobs/ml_save_model.py` | The application: live numbers from HDFS, a "Check a request" attack tester running the saved Spark ML model, every chart an embedded Superset dashboard |

## Run it

Needs Docker Desktop (8 GB memory for Docker), `make`, `curl`.

```bash
make data          # download the NASA logs into ./data (356 MB)
make build         # build the images: Hadoop 3.3.6 + Spark 3.5.3, and Apache Superset 4.1.1
make up            # start the 10 containers, wait for HDFS
make skeleton      # ingest -> Spark ETL -> 11 Spark SQL reports -> Z-score anomalies
make bots          # GMM bot detection on the NASA hosts
make ml            # attack data -> HDFS -> preprocessing -> 8 models -> output/ml/ml_report.html
make bi            # publish the ML tables, register every table in Spark SQL, build the dashboards, start the app
```

On a Mac, `run_dashboards.command` (double-click) does `build`, `up` and only the stages whose results
are missing, then opens the app.

| URL | What |
|---|---|
| http://localhost:3080 | **LogLens**, the application (no login) |
| http://localhost:8088 | **Dashboards** (Apache Superset), login `admin` / `admin` |
| http://localhost:9870 | HDFS NameNode |
| http://localhost:8080 | Spark master (the dashboard queries run as the `thrift-server` application) |
| `jdbc:hive2://localhost:10000/weblogs` | Spark SQL over JDBC (Spark Thrift Server), for any BI tool |

### The frontend: LogLens + Apache Superset

The frontend uses only big data visualisation tools for its charts. **LogLens** (`app/`, port 3080) is the
application around them, in the style of a product dashboard: a sidebar, a home page, and pages for traffic,
errors, anomalies, bots and model performance. It draws no charts itself:

- **Every chart** is an **Apache Superset** dashboard embedded in the page (Superset *embedded mode*). The app's
  server asks Superset for a short-lived guest token that can view only that one dashboard; Superset's own SDK
  renders it. Superset reads the Parquet tables in HDFS through the **Spark Thrift Server**, so every chart is a
  Spark SQL query on the cluster.
- **Every number** on the home page (requests, hosts, GB, error rate, unusual hours, automated hosts, F1) is a
  Spark query on HDFS, run by the app's own SparkSession when it starts.
- **Check a request** runs the saved **Spark ML model** (`hdfs:///models/fm_attack`, the Factorization Machine
  with F1 0.9669) on any URL you type, and explains the result.
- **Data & pipeline** shows what is in each HDFS zone right now, live from the NameNode (WebHDFS).

```
HDFS (Parquet) -> Spark Thrift Server :10000 (Spark SQL) -> Apache Superset :8088 -> LogLens app :3080
             \-> Spark ML model + Spark queries ------------------------------------^
```

Screenshots: `docs/app/` (the app) and `docs/dashboards/` (the Superset dashboards).

| Superset dashboard | What is on it | Spark tables |
|---|---|---|
| 0 · Home | Requests per day, status codes, hour of day, F1 of the 8 models (on the app's home page) | `daily`, `status_distribution`, `by_hour`, `ml_comparison` |
| 1 · Traffic overview | Requests, unique hosts, GB served, requests per day, by hour and weekday, top URLs, file types | `access_logs`, `daily`, `by_hour`, `by_weekday`, `top_urls`, `by_content_type` |
| 2 · Errors and performance | Error rate, 404s, 5xx, status codes, daily error rate, broken links, response sizes, heaviest clients | `status_distribution`, `errors_daily`, `broken_links`, `size_buckets`, `size_percentiles`, `heaviest_hosts` |
| 3 · Anomaly detection (Z-score) | Requests per hour for the whole period, the 12 unusual hours against their normal level, the anomaly day | `anomaly_hours`, `anomaly_days`, `access_logs`, `daily` |
| 4 · Bot detection (GMM) | Hosts and requests by segment, behaviour of the 3 GMM components, the automated hosts | `host_clusters`, `bot_clusters`, `bot_summary` |
| 5 · Attack detection (ML) | F1 of the 8 models, precision/recall, confusion matrix with a model filter, 5-split stability, McNemar, cross-dataset, tuning grid, NASA audit | `ml_*` (from `jobs/publish_ml_tables.py`) |

**SQL Lab** (Superset menu → SQL → SQL Lab) runs any Spark SQL on the `weblogs` database, e.g.
`SELECT host, COUNT(*) FROM weblogs.access_logs GROUP BY host ORDER BY 2 DESC LIMIT 10`.

How it is wired:
- `sql/bi_tables.sql` registers each HDFS folder as a Spark table (`make bi-tables`, run through `beeline`).
- `jobs/publish_ml_tables.py` turns the ML result files into Spark tables (`make ml-publish`).
- `superset/build_dashboards.py` creates the connection, datasets, charts and dashboards through Superset's
  REST API and enables embedded mode on each (`make dashboards`); it is the single source of every dashboard.
- `jobs/ml_save_model.py` saves the Factorization Machine to HDFS for the app's checker (`make ml-model`).
- `app/` is the LogLens app (Flask + the Superset embedded SDK); `make app` restarts it.


Run `make` on its own to list every target. On a Mac, double-click `run_everything.command` to build, start
and run everything; on Windows, `run_pipeline.bat`. No Docker? Open `notebooks/LogAnalytics_Colab.ipynb` in
Google Colab and use Runtime → Run all.

## Results

**Preprocessing:** raw text → 11 typed columns (`docs/preprocessing_*.png`).

**Scalability** (`make bench`; ETL + 11 reports, Spark local mode on one 2-core machine, median of 2 runs):

| Data | Lines | 1 core | 2 cores | Speed-up | Throughput (2 cores) |
|---|---|---|---|---|---|
| 25% | 865,403 | 60.9 s | 53.1 s | 1.15× | 16,296 lines/s |
| 50% | 1,730,806 | 92.6 s | 74.6 s | 1.24× | 23,214 lines/s |
| 100% | 3,461,613 | 161.0 s | 123.0 s | 1.31× | 28,140 lines/s |
| 200% | 6,923,225 | 275.9 s | 187.3 s | 1.47× | 36,968 lines/s |

Doubling the data adds well under double the time, because Spark's fixed start-up cost (~5–8 s per job) is
spread over more lines, and a second core helps more as the data grows. A multi-node test on the Docker
cluster at 5× and 10× is future work.

**Machine learning** (test set of 261,076 labelled requests):

| Model | Precision | Recall | F1 |
|---|---|---|---|
| Linear SVM | 0.9494 | 0.9885 | 0.9686 |
| **Factorization Machine (ours)** | 0.9498 | 0.9846 | **0.9669** |
| Logistic Regression | 0.9453 | 0.9854 | 0.9649 |
| Decision Tree | 0.9292 | 0.9586 | 0.9437 |
| Gradient Boosted Trees | 0.8185 | 0.9609 | 0.8840 |
| Naive Bayes | 0.9146 | 0.8033 | 0.8553 |
| Multilayer Perceptron | 0.7738 | 0.9379 | 0.8480 |
| Random Forest | 0.9571 | 0.7109 | 0.8158 |

![Confusion matrices](docs/ml_results/confusion_matrices_all.png)

**Stability (5 random splits, mean ± std F1):** Linear SVM 0.9699 ± 0.0011, Logistic Regression
0.9663 ± 0.0009, Factorization Machine 0.9611 ± 0.0062. Inside one dataset the SVM is significantly better
(McNemar p < 0.05 on 4 of 5 splits).

**Across datasets (5 seeds):** trained on Fwaf and tested on HttpParams, the Factorization Machine keeps
F1 0.968 ± 0.008, against 0.897 for Logistic Regression and 0.848 for Linear SVM, with 6–14× fewer false
alarms. That is why the FM is the deployed model. Settings were chosen on a validation split, never the
test set. Details in [ML.md](ML.md).

**Bot detection (unsupervised, on the NASA logs themselves):** hosts with at least 20 requests are
split into sessions (30-minute gap), described by 9 behaviour features (log-scaled and standardised),
and clustered with a 3-component Gaussian Mixture Model (silhouette 0.49). One component holds the
automated clients: 1,829 hosts, 4.8% of clustered hosts and 6.4% of requests. GMM was chosen over
K-Means because the automated group is small and elongated; K-Means either absorbs it (k = 2) or
splits it apart (k = 8).

**NASA logs:** the FM flags 120 of 31,026 URLs (1,754 requests, 0.05%). Checked by hand, none is an attack
(searches for "select", pages under /msfc/de**script**ion/, malformed requests); see ML.md.

## Tests

```bash
pip install -r requirements-dev.txt   # needs Java 11
make test                             # = pytest
```

The tests run every Spark job unchanged (ETL, 11 reports, Z-score, bot detection, ML preprocessing, tuning,
training, cross-dataset, robustness) in Spark local mode on a small sample with known answers: exact parse
of hand-written log lines, the one malformed line kept as a reject, report totals that add up, an injected
traffic burst that the Z-score must find, no request in both train and test, valid confusion matrices,
the ML tables for the dashboards, and a real Spark Thrift Server that must serve every dashboard table. GitHub Actions runs them on every push (`.github/workflows/tests.yml`).

`make bench` measures how the ETL and reports scale with data size and CPU cores (see Results).

## Team guides

[KRITI.md](KRITI.md) (HDFS cluster) · [KUSHAL.md](KUSHAL.md) (Spark ETL) · [GARGI.md](GARGI.md) (Spark SQL + Superset dashboards) · [ML.md](ML.md) (machine learning)


## Layout

```
docker/            Dockerfile + entrypoint.sh (one image, a role per container)
docker-compose.yml the cluster: NameNode, 2 DataNodes, Spark master + worker, client,
                   Spark Thrift Server, Apache Superset, LogLens app
conf/              core-site.xml, hdfs-site.xml, spark-defaults.conf
jobs/              Spark jobs: etl, reports, anomalies, bot_detection, ml_preprocess, ml_tune,
                   ml_train, ml_save_model, ml_crossdataset, ml_robustness, publish_ml_tables
sql/               bi_tables.sql: the HDFS tables the dashboards read (Spark Thrift Server)
app/               LogLens, the application frontend (embeds the Superset dashboards)
superset/          Apache Superset image, config and build_dashboards.py (the 5 dashboards)
scripts/           data download, NASA flag audit, scalability benchmark
tests/             end-to-end tests (pytest) + small fixtures
.github/workflows/ CI: runs the tests on every push
notebooks/         Google Colab version of the pipeline (+ the 50% review and v1 copies)
docs/              figures, dashboard screenshots and ML results; docs/reviews/ has the decks, the 50% report,
                   the speaker script and the earlier README and team notes
data/, output/     created locally, not in git
```
