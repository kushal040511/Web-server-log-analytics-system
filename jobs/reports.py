#!/usr/bin/env python3
"""
Stage 2 of the pipeline — the four core reports, as Spark SQL.

Reads the curated Parquet layer, registers it as a temporary view, and answers
every question in plain SQL. Results are written back to HDFS as Parquet report
tables and printed so you can eyeball them.

    docker compose exec client spark-submit /workspace/jobs/reports.py
"""
import sys
from pyspark.sql import SparkSession

CURATED = sys.argv[1] if len(sys.argv) > 1 else "hdfs://namenode:8020/data/weblogs/curated"
REPORTS = sys.argv[2] if len(sys.argv) > 2 else "hdfs://namenode:8020/data/weblogs/reports"

spark = SparkSession.builder.appName("weblog-reports").getOrCreate()
spark.sparkContext.setLogLevel("WARN")

logs = spark.read.parquet(CURATED)
logs.createOrReplaceTempView("access_logs")
logs.cache()
print(f"[reports] {logs.count():,} records from {CURATED}")

R = {}

# The "top N" reports are capped so a report table stays a report rather than a
# copy of the whole log. 300 is chosen to be worth searching and paginating in
# the dashboard tables; a cap of 25 would leave the dashboard's search box
# searching a table already cut down to one screenful.
def run(name, sql, limit=None, show=8):
    df = spark.sql(sql)
    if limit:
        df = df.limit(limit)
    df.write.mode("overwrite").parquet(f"{REPORTS}/{name}")
    rows = [r.asDict() for r in df.collect()]
    R[name] = rows
    print(f"\n=== {name} ===")
    for r in rows[:show]:
        print("   ", r)
    return rows

# ------------------------------------------------- 1. TRAFFIC: peak hours
run("by_hour", """
    SELECT hour,
           COUNT(*)                             AS hits,
           COUNT(DISTINCT host)                 AS visitors,
           ROUND(SUM(bytes)/1024/1024/1024, 2)  AS gb
    FROM access_logs
    GROUP BY hour
    ORDER BY hour
""")

run("daily", """
    SELECT date,
           COUNT(*)              AS hits,
           COUNT(DISTINCT host)  AS visitors
    FROM access_logs
    GROUP BY date
    ORDER BY date
""")

run("by_weekday", """
    SELECT dow, COUNT(*) AS hits
    FROM access_logs
    GROUP BY dow
""")

# --------------------------------------------- 2. CONTENT: most-visited URLs
run("top_urls", """
    SELECT endpoint, COUNT(*) AS hits
    FROM access_logs
    WHERE status = 200
      AND ext NOT IN ('gif','jpg','jpeg','xbm','png','ico')
    GROUP BY endpoint
    ORDER BY hits DESC
""", limit=300)

run("by_content_type", """
    SELECT CASE WHEN ext = '' THEN '(none)' ELSE ext END AS ext,
           COUNT(*)                            AS hits,
           ROUND(SUM(bytes)/1024/1024/1024, 2) AS gb
    FROM access_logs
    GROUP BY ext
    ORDER BY hits DESC
""", limit=40)

# ------------------------------------------------------ 3. ERRORS: 404 trends
run("status_distribution", """
    SELECT status,
           COUNT(*) AS hits,
           ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM access_logs), 3) AS pct
    FROM access_logs
    GROUP BY status
    ORDER BY hits DESC
""")

run("errors_daily", """
    SELECT date,
           COUNT(*) AS total,
           SUM(CASE WHEN status = 404 THEN 1 ELSE 0 END) AS not_found,
           ROUND(100.0 * SUM(CASE WHEN status = 404 THEN 1 ELSE 0 END) / COUNT(*), 3) AS pct_404,
           ROUND(100.0 * SUM(CASE WHEN status >= 500 THEN 1 ELSE 0 END) / COUNT(*), 4) AS pct_5xx
    FROM access_logs
    GROUP BY date
    ORDER BY date
""")

run("broken_links", """
    SELECT endpoint, COUNT(*) AS hits, COUNT(DISTINCT host) AS distinct_visitors
    FROM access_logs
    WHERE status = 404
    GROUP BY endpoint
    ORDER BY hits DESC
""", limit=300)

# ------------------------------------------ 4. PERFORMANCE: response weight
run("size_percentiles", """
    SELECT percentile_approx(bytes, 0.50) AS p50,
           percentile_approx(bytes, 0.90) AS p90,
           percentile_approx(bytes, 0.95) AS p95,
           percentile_approx(bytes, 0.99) AS p99,
           MAX(bytes)                     AS max_bytes,
           ROUND(AVG(bytes), 0)           AS mean_bytes
    FROM access_logs
    WHERE status = 200 AND bytes > 0
""")

run("size_buckets", """
    SELECT CASE
             WHEN bytes = 0       THEN '0 (no body)'
             WHEN bytes < 1024    THEN '<1 KB'
             WHEN bytes < 10240   THEN '1-10 KB'
             WHEN bytes < 102400  THEN '10-100 KB'
             WHEN bytes < 1048576 THEN '100 KB-1 MB'
             ELSE '>1 MB' END AS bucket,
           COUNT(*) AS hits
    FROM access_logs
    WHERE status = 200
    GROUP BY bucket
""")

run("heaviest_hosts", """
    SELECT host,
           COUNT(*)                       AS hits,
           ROUND(SUM(bytes)/1024/1024, 1) AS mb
    FROM access_logs
    GROUP BY host
    ORDER BY hits DESC
""", limit=300)

print(f"\n[reports] {len(R)} report tables written to {REPORTS}")
print("[reports] read one back with:")
print(f"[reports]   make report T=top_urls N=10")

spark.stop()
