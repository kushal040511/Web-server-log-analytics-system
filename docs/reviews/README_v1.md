# Log Analytics System for a Web Server

A batch pipeline that turns raw Apache/Nginx access logs into traffic, error and
performance reports — running on a real HDFS + Spark cluster in Docker.

```
./data/*.log ──▶ HDFS /data/weblogs/raw ──▶ Spark ETL ──▶ HDFS /data/weblogs/curated
                       (immutable)          (regex parse)   (Parquet, by date)
                                                                    │
                                                     ┌──────────────┴──────────────┐
                                                     ▼                             ▼
                                          Spark SQL reports              GMM bot detection
                                       /data/weblogs/reports              (Spark MLlib)
                                                     │
                                                     ▼
                                       ./output/dashboard.json ──▶ the website :8050
                                       ./output/tables/*.csv|json   (7 pages + downloads)
```

---

## A note on the dataset

The NASA-HTTP trace has a real hole in it: **four days are missing entirely —
29, 30 and 31 July, and 2 August 1995** — and the days on either side (28 July,
1 and 3 August) are partial. The dashboard shades that gap rather than drawing
a straight line across it, and no job interpolates the missing values. If a
chart looks like traffic collapsed in late July, that is the gap, not a crash.

## Before you start

You need **Docker Desktop** with at least **8 GB of memory** allocated
(Settings → Resources → Memory). The default 4 GB is not enough to run a
NameNode, two DataNodes, a Spark master and a worker at once.

```bash
docker info --format '{{.MemTotal}}' | awk '{printf "Docker memory: %.1f GB\n", $1/1073741824}'
```

Everything is architecture-independent — the image builds from Apache tarballs
on a multi-arch JDK base, so it runs natively on Apple Silicon and Intel alike.
No emulation, no `platform: linux/amd64`.

---

## Run it

Four commands, start to finish:

```bash
make data       # download the NASA-HTTP logs  (~37 MB down, 356 MB extracted)
make build      # build the image              (~1.1 GB of downloads, one time)
make up         # start the cluster and wait for HDFS
make skeleton   # ingest -> parse -> report -> dashboard
```

`make skeleton` runs the whole thin slice: raw logs into HDFS, Spark parses them
into partitioned Parquet, eleven Spark SQL reports come out, and the dashboard
JSON is exported.

Then look at it:

| What | Where |
|---|---|
| **The website** — seven pages of reports | **<http://localhost:8050>** |
| HDFS — browse the files, see block placement | <http://localhost:9870> |
| Spark master — running and completed jobs | <http://localhost:8080> |
| Spark worker | <http://localhost:8081> |

### The website

Seven pages, all served by the `web` container:

| Page | What is on it |
|---|---|
| `/` Overview | KPI row, daily volume, status mix, top pages and broken links |
| `/traffic` | Hour-of-day and weekday profiles, daily volume, content types, searchable URL table |
| `/errors` | 404 rate over time, status-code breakdown, the ranked broken-link table |
| `/performance` | Response-size percentiles and buckets, bandwidth by type, heaviest clients |
| `/bots` | The GMM result: clusters, behavioural signature, and how it was validated |
| `/sql` | A read-only Spark SQL console against the curated Parquet |
| `/about` | How the pipeline is built, and which report table each number comes from |

Every table can be searched, sorted and paged, and downloaded as CSV or JSON.
The date-range control at the top of the report pages filters the daily series,
the hour-of-day profile and the status mix — the hour × date and status × date
cross-tabs are exported precisely so that filter recomputes real numbers rather
than re-labelling period-wide totals. The range follows you between pages.

Where a figure cannot be recomputed for a range — a distinct count cannot be
summed back up from per-day totals — the page labels it **· all dates** instead
of letting it look filtered.

After editing anything under `web/`, `make site` re-exports the data and
restarts the service. Templates and CSS are re-read on each request, so a
template edit alone only needs a browser reload.

#### The SQL page needs the cluster

Every other page reads `./output/dashboard.json` and works with the cluster
stopped. `/sql` opens a real SparkSession against `hdfs://namenode:8020` and
queries the curated Parquet directly. The session is built on the first query —
which is why the first one takes ~30 seconds and the rest are quick — and the
page says so while it waits. With the cluster down it returns a clear error, not
a blank screen.

It accepts `SELECT`, `WITH`, `SHOW`, `DESCRIBE` and `EXPLAIN` only, and caps
results at 500 rows. That guard is a convenience, not a security boundary: the
cluster has no authentication and should not be exposed beyond localhost.

```bash
make ls                        # what is in HDFS
make report T=top_urls N=10    # print a report table
make report T=errors_daily N=15
make fsck                      # prove every block really has 2 replicas
make bots                      # run the GMM automated-traffic detector
make dashboard                 # re-export after bots, so the /bots page fills in
make site                      # re-export and restart the website
```

### A note on the "top N" tables

`top_urls`, `broken_links` and `heaviest_hosts` are capped at 300 rows and
`by_content_type` at 40, in `jobs/reports.py`. A report table is a report, not a
second copy of the log.

The site never counts those rows and calls the result a finding — "300 distinct
broken URLs" would just be the `LIMIT` read back as if it meant something. True
totals (`not_found_total`, `not_found_urls`, `visitors`, `endpoints`) come from
the full curated data in `jobs/export_dashboard.py`, and each capped table says
on the page what it is a subset of.

### A copy you can hand in

```bash
make standalone      # -> output/site/  and  output/site.zip
```

That renders every report page to plain HTML with the data embedded, the CSS and
JS beside it, and the CSV/JSON tables included. No server, no Docker, no network
— open `output/site/index.html` and it works, or send the zip.

The SQL page is not in the export, and that is deliberate: it is the one page
that runs live queries against the cluster, so a copy of it would be a button
that always fails.

`make` on its own lists every target.

---

## What each piece does

| File | Role |
|---|---|
| `docker/Dockerfile` | One image: JDK 11 + Hadoop 3.3.6 + Spark 3.5.3 + numpy + flask |
| `docker/entrypoint.sh` | Selects the role — namenode, datanode, spark-master, web, … |
| `docker-compose.yml` | 1 NameNode, 2 DataNodes, Spark master + worker, client, web |
| `conf/core-site.xml` | Where HDFS lives (`hdfs://namenode:8020`) |
| `conf/hdfs-site.xml` | Replication factor 2, storage dirs, permissions off |
| `conf/spark-defaults.conf` | Master URL, event log, warehouse dir, shuffle partitions |
| `jobs/etl.py` | Raw text → typed columns → Parquet partitioned by date |
| `jobs/reports.py` | The four core reports, expressed as Spark SQL |
| `jobs/bot_detection.py` | Gaussian Mixture Model over per-host behavioural features |
| `jobs/export_dashboard.py` | Report tables → `dashboard.json` + `tables/*.csv|json` |
| `web/app.py` | Flask: the seven pages, the JSON API, downloads, the SQL endpoint |
| `web/templates/` | `base.html` plus one template per page |
| `web/static/css/app.css` | The whole design system — light/dark tokens, grid, tables |
| `web/static/js/charts.js` | Hand-drawn SVG charts; no charting library, no CDN |
| `web/static/js/app.js` | Data store, date-range control, the sortable/searchable table |
| `scripts/make_standalone.py` | Renders the site to static files that need no server |
| `scripts/get_data.sh` | Downloads the dataset |

### Two storage zones, and why

`raw` is written once and never edited — it is the audit trail. `curated` is
always regenerable from `raw`. That is what makes the pipeline safe to iterate
on: if the parsing regex turns out to be wrong, fix it, re-run `make etl`, and
nothing is lost. Every real data platform is built this way.

### Why the report pages are deliberately dumb

Every page except `/sql` renders `./output/dashboard.json` and nothing else — no
database connection, no query logic, no access to HDFS. Every number was
computed by Spark and can be traced to a query in `jobs/reports.py`. Two things
follow: those pages cannot produce a wrong answer, because they have no logic to
get wrong; and they keep working when the cluster is shut down, which matters the
morning of a demo.

`/sql` is the deliberate exception, and it is the only page that needs the
cluster up.

### Why the reports are SQL

Because they should be readable by someone who is not you. `jobs/reports.py` is
mostly a list of SQL strings — paste any of them into `make sql` and run it.

### Why a Gaussian Mixture Model, not K-Means

Automated hosts are 0.07% of all hosts. K-Means minimises total within-cluster
variance and assigns hard labels, so a group that small either gets absorbed
(k=2, worse than random) or shattered (k=8, 15% recall). GMM fits a full
covariance matrix per component, so it can model a small, elongated cluster, and
returns a membership probability rather than a hard label. Measured across
k = 2–8, GMM beat both K-Means and Bisecting K-Means at every recall level.

---

## Verifying it actually works

The point of a distributed system is the properties you can demonstrate:

```bash
# 1. Data is really split into blocks across two machines
make fsck

# 2. Replication is real — kill a DataNode, data is still readable
docker compose stop datanode2
make report T=top_urls N=5          # still works
docker compose start datanode2

# 3. The report pages survive the cluster (only /sql needs it)
docker compose stop namenode datanode1 datanode2 spark-master spark-worker
open http://localhost:8050          # still renders — data is already exported

# 4. Partition pruning is real — a one-day query does not read 62 days
docker compose exec client spark-sql -e \
  "SELECT COUNT(*) FROM parquet.\`hdfs://namenode:8020/data/weblogs/curated\` WHERE date = '1995-07-04'"
```

---

## Troubleshooting

**`make up` times out waiting for HDFS.**
Look at `make logs`. The usual cause is Docker running out of memory — raise it
to 8 GB. If the NameNode complains about an inconsistent storage directory, the
filesystem image is from an older run: `make destroy && make up`.

**DataNodes start but the NameNode reports 0 live nodes.**
Usually a stale cluster ID after a re-format. `make destroy && make up`.

**The build fails downloading Hadoop or Spark.**
`archive.apache.org` throttles occasionally. Re-run `make build` — Docker caches
completed layers, so it resumes rather than starting over. To pin different
versions, edit the `ARG` lines at the top of `docker/Dockerfile`.

**Spark jobs fail with `Java gateway process exited`.**
Almost always memory. Lower `spark.executor.memory` in
`conf/spark-defaults.conf`, or give Docker more.

**The website says "No data yet".**
`./output/dashboard.json` has not been written. Run `make dashboard` (it needs
`make reports` to have run first). Check `curl localhost:8050/healthz`.

**The `/sql` page says it cannot reach the cluster.**
That page is the one that needs HDFS and Spark running: `make up`. Every other
page works without them.

**The `/bots` page says the model hasn't run.**
`make bots && make dashboard`. It is a separate stage because it takes a few
minutes.

**A page shows stale numbers after re-running a job.**
The browser is holding the old JSON. `make site` restarts the service; a hard
reload settles the rest.

**`make bots` fails on `import numpy`.**
The image installs numpy for `pyspark.ml`. If you changed the Dockerfile, make
sure that line survived, then `make build` again.

**A port is already in use.**
Change only the *host* side of the mapping in `docker-compose.yml` —
`"8080:8080"` becomes `"18088:8080"`. Never change the container side; the
services find each other by those. Note 18080 is already taken by the optional
history server, so don't reuse it.

---

## Where this is going

Built and working:

- [x] HDFS with replication, two storage zones
- [x] Spark ETL: regex parse, type cast, reject handling, partitioned Parquet
- [x] Spark SQL reports: traffic, content, errors, performance
- [x] GMM automated-traffic detection
- [x] Seven-page website: searchable/sortable tables, CSV and JSON downloads,
      date-range drill-down, dark mode, and a live Spark SQL console

Still ahead:

- [ ] Standalone Hive Metastore + HiveServer2, so the tables are queryable by
      any SQL client rather than only through Spark
- [ ] Flume or Logstash ingestion, replacing the manual `hdfs dfs -put`
- [ ] Scheduling (cron or Oozie) for a nightly run
- [ ] Benchmarking: query runtimes with and without partition pruning
