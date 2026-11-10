# Gargi — Spark SQL reports and the BI dashboards (Apache Superset)

I built the analysis and presentation side: the Spark SQL reports, the Z-score anomaly
detection, and the frontend, which uses only big data visualisation tools: **Apache Superset**
reading the HDFS tables through the **Spark Thrift Server** (Spark SQL over JDBC).

| My file | What it does |
|---|---|
| `jobs/reports.py` | The 11 Spark SQL reports, written to HDFS as Parquet |
| `jobs/anomalies.py` | Z-score anomaly detection per hour of day and per day (Spark SQL) |
| `sql/bi_tables.sql` | Registers every HDFS folder as a Spark table in the `weblogs` database |
| `docker/entrypoint.sh` (`thrift` role) | Starts the Spark Thrift Server on the cluster (1 core, 1 GB) |
| `superset/Dockerfile`, `superset_config.py`, `bootstrap.sh` | The Superset container with the Spark driver |
| `superset/build_dashboards.py` | Creates the connection, 32 datasets, 56 charts and 6 dashboards through Superset's REST API, with embedded mode on |
| `jobs/publish_ml_tables.py` | Turns the ML results into Spark tables so the dashboards can chart them |
| `app/` (team) | LogLens, the application: embeds the Superset dashboards with guest tokens |

```
HDFS (Parquet)  ->  Spark Thrift Server :10000  ->  Apache Superset :8088  ->  LogLens app :3000
```

## Demo (about 4 minutes)

1. `make bi`, then open the app at http://localhost:3000 (Superset itself: http://localhost:8088, `admin` / `admin`).
2. **1 · Traffic overview** — the KPIs, requests per day, the hour-of-day and weekday profiles, top URLs.
3. **2 · Errors and performance** — status codes, the daily 404 rate, broken links, response sizes.
4. **3 · Anomaly detection** — the hourly traffic spike on 13 July and the 12 unusual hours against their normal level.
5. Open any chart's menu → **View query**: it is Spark SQL, run on the cluster. Show the query in the
   Spark master UI (http://localhost:8080, application `thrift-server (dashboards)`).
6. **SQL Lab**: run `SELECT status, COUNT(*) FROM weblogs.access_logs GROUP BY status` live.

## What to say — the findings

| Finding | Number |
|---|---|
| Requests analysed | 3,461,612 from 137,978 hosts, 61 GB served |
| Successful (200) | 89.6% |
| Not found (404) | 20,899 requests (0.6%) |
| Most-visited page | `/ksc.html`, 79,993 hits |
| Worst broken link | `/pub/winvn/readme.txt`, 2,004 hits |
| Busiest hour / day | 15:00 (230,665 hits) / Thursday (667,737) |
| Typical response size | median 4,179 bytes; 99th percentile 283,389 bytes |
| Unusual hours (Z-score > 3) | 12, 11 of them on 13 July 1995 (STS-70 launch); peak z = 5.90 at 09:00 |

- Every number on the dashboards is a Spark SQL query on HDFS; Superset only draws it.
- Nothing is copied out of HDFS. Results are cached for an hour because the tables only change when the pipeline runs.

## Likely questions

- *Why Superset?* — It is the Apache big data BI tool: it speaks SQL to big data engines (Spark, Hive, Presto, Druid)
  instead of loading data into the browser, and it gives dashboards, filters and SQL Lab without custom frontend code.
- *Why the Spark Thrift Server?* — It exposes Spark SQL over JDBC/ODBC, so any BI tool (Superset, Power BI, Tableau)
  can query the HDFS tables with the cluster doing the work.
- *Why is traffic flat in late July?* — Days are missing from the dataset (a server outage), not a crash of our pipeline.
- *Why only 300 rows in the top-URL table?* — A report table is capped; the true totals come from `access_logs`.
- *Is the app a charting library?* — No. LogLens only lays out the pages; each chart in it is an Apache Superset
  dashboard embedded with a guest token that can view only that dashboard. The Superset SDK draws it.
