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
| Analyse | **Spark SQL** | Gargi | `jobs/reports.py`, `jobs/anomalies.py`, `jobs/export_dashboard.py` | 11 reports; Z-score finds 12 unusual hours (13 July 1995, STS-70 launch) |
| Detect bots | **Spark MLlib** (unsupervised) | Team | `jobs/bot_detection.py` | Gaussian Mixture Model on 9 per-host behaviour features; 1,829 of 38,104 hosts (4.8%, 6.4% of requests) flagged as automated, checked against hosts that fetched `/robots.txt` |
| Detect attacks | **Spark MLlib** | Team | `jobs/ml_preprocess.py`, `jobs/ml_train.py`, `jobs/ml_crossdataset.py` | Factorization Machine, F1 0.967, compared with 7 models (see [ML.md](ML.md)) |

## Run it

Needs Docker Desktop (8 GB memory for Docker), `make`, `curl`.

```bash
make data          # download the NASA logs into ./data (356 MB)
make build         # build the image: Java 11, Hadoop 3.3.6, Spark 3.5.3
make up            # start the 7 containers, wait for HDFS
make skeleton      # ingest -> etl -> reports -> anomalies -> dashboard
make bots          # GMM bot detection on the NASA hosts, then: make dashboard
make ml            # attack data -> HDFS -> preprocessing -> 8 models -> output/ml/ml_report.html
```

| URL | What |
|---|---|
| http://localhost:8050 | Website: 9 pages (below) |
| http://localhost:9870 | HDFS NameNode |
| http://localhost:8080 | Spark master |

### The website

| Page | What is on it | Needs |
|---|---|---|
| `/` Overview | KPI row, daily volume, status mix, top pages and broken links | `make dashboard` |
| `/logs` Log explorer | Search and replay the raw access log line by line | the files in `./data` |
| `/traffic` | Hour-of-day and weekday profiles, content types, Z-score unusual hours | `make dashboard` |
| `/errors` | 404 rate over time, status codes, ranked broken links | `make dashboard` |
| `/performance` | Response-size percentiles and buckets, heaviest clients | `make dashboard` |
| `/bots` | GMM bot detection: clusters, behaviour signature, validation | `make bots && make dashboard` |
| `/ml` Attack detection | Factorization Machine vs 7 models, cross-dataset, stability, NASA audit | `make ml` |
| `/sql` | Read-only live Spark SQL console on the curated Parquet | the cluster running (`make up`) |
| `/about` | How the pipeline is built and where each number comes from | — |

Every table can be searched, sorted, paged and downloaded as CSV or JSON. Every page except `/sql`
reads files Spark already wrote, so it keeps working with the cluster stopped. `/sql` accepts
`SELECT`, `WITH`, `SHOW`, `DESCRIBE` and `EXPLAIN` only and caps results at 500 rows.

`make standalone` writes `output/site/` and `output/site.zip`: the report pages as plain HTML with
the data inside, which open with no server. `/sql`, `/logs` and `/ml` are left out because they need
a live server.

Run `make` on its own to list every target. On a Mac, double-click `run_pipeline.command` to build, start and run everything. No Docker? Open `notebooks/LogAnalytics_Colab.ipynb`
in Google Colab and use Runtime → Run all.

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

The tests run every Spark job unchanged (ETL, 11 reports, Z-score, export, ML preprocessing, tuning,
training, cross-dataset, robustness) in Spark local mode on a small sample with known answers: exact parse
of hand-written log lines, the one malformed line kept as a reject, report totals that add up, an injected
traffic burst that the Z-score must find, no request in both train and test, valid confusion matrices,
and every website page and API. GitHub Actions runs them on every push (`.github/workflows/tests.yml`).

`make bench` measures how the ETL and reports scale with data size and CPU cores (see Results).

## Team guides

[KRITI.md](KRITI.md) (HDFS cluster) · [KUSHAL.md](KUSHAL.md) (Spark ETL) · [GARGI.md](GARGI.md) (Spark SQL + website) · [ML.md](ML.md) (machine learning)

To run only the website (no Docker): `pip install -r requirements-gargi.txt`, then
`cd web && OUTPUT_DIR=../output python app.py` after the pipeline has filled `output/`.

## Layout

```
docker/            Dockerfile + entrypoint.sh (one image, a role per container)
docker-compose.yml the 7-container cluster
conf/              core-site.xml, hdfs-site.xml, spark-defaults.conf
jobs/              Spark jobs: etl, reports, anomalies, bot_detection, export_dashboard,
                   ml_preprocess, ml_tune, ml_train, ml_crossdataset, ml_robustness
web/               Flask website: Overview, Log explorer, Traffic (with Z-score anomalies), Errors,
                   Performance, Bot detection, Attack detection, SQL console, About
scripts/           data download, NASA flag audit, scalability benchmark, standalone site export
tests/             end-to-end tests (pytest) + small fixtures
.github/workflows/ CI: runs the tests on every push
notebooks/         Google Colab version of the pipeline (+ the 50% review and v1 copies)
docs/              figures and ML results; docs/reviews/ has the decks, the 50% report,
                   the speaker script and the earlier README and team notes
data/, output/     created locally, not in git
```
