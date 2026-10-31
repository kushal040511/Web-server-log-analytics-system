#!/usr/bin/env python3
"""
Stage 2b of the pipeline: Z-score anomaly detection, written in plain Spark SQL.
No MLlib and no extra library: only GROUP BY, AVG, STDDEV_POP and a JOIN.

Question it answers: which hours (and days) had far more, or far less, traffic
than is normal for that time?

The algorithm
  1. Count requests for every (date, hour) in the curated data.
  2. Baseline: for each hour of the day (0-23), the mean and the standard deviation
     of those counts across all days. Each hour is compared only with the same hour
     on other days, so a busy afternoon is not flagged just for being an afternoon.
  3. Z-score of every (date, hour):   z = (hits - mean) / std
     z tells how many standard deviations a value sits from normal.
  4. |z| > 3 is an anomaly: under a normal distribution only about 0.3% of values
     fall that far out. z > 3 is a spike, z < -3 is a drop.
  The same is done per day against the mean and std of all days.

Reads the curated Parquet, writes two report tables to the HDFS reports zone:
  anomaly_hours   every flagged (date, hour), strongest first
  anomaly_days    every flagged day

    docker compose exec client spark-submit /workspace/jobs/anomalies.py
"""
import sys
from pyspark.sql import SparkSession

CURATED = sys.argv[1] if len(sys.argv) > 1 else "hdfs://namenode:8020/data/weblogs/curated"
REPORTS = sys.argv[2] if len(sys.argv) > 2 else "hdfs://namenode:8020/data/weblogs/reports"
THRESHOLD = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0

spark = SparkSession.builder.appName("weblog-anomalies").getOrCreate()
spark.sparkContext.setLogLevel("WARN")

spark.read.parquet(CURATED).createOrReplaceTempView("access_logs")
print(f"[anomalies] Z-score anomaly detection, threshold |z| > {THRESHOLD}")


def save(name, df):
    df.write.mode("overwrite").parquet(f"{REPORTS}/{name}")
    rows = df.collect()
    print(f"\n=== {name}: {len(rows)} flagged ===")
    for r in rows[:12]:
        print("   ", r.asDict())
    return rows


# ------------------------------------------------------------ 1. HOURLY
# step 1 counts, step 2 baseline per hour of day, step 3 z-score, step 4 flag
hours = spark.sql(f"""
    WITH hourly AS (
        SELECT date, hour, COUNT(*) AS hits
        FROM access_logs
        GROUP BY date, hour
    ),
    baseline AS (
        SELECT hour,
               AVG(hits)        AS mean_hits,
               STDDEV_POP(hits) AS std_hits
        FROM hourly
        GROUP BY hour
    ),
    scored AS (
        SELECT h.date, h.hour, h.hits,
               ROUND(b.mean_hits, 1)                        AS normal_hits,
               ROUND(b.std_hits, 1)                         AS std_hits,
               ROUND((h.hits - b.mean_hits) / b.std_hits, 2) AS z
        FROM hourly h
        JOIN baseline b ON h.hour = b.hour
    )
    SELECT date, hour, hits, normal_hits, std_hits, z,
           CASE WHEN z > 0 THEN 'spike' ELSE 'drop' END AS kind
    FROM scored
    WHERE ABS(z) > {THRESHOLD}
    ORDER BY ABS(z) DESC
""")
hour_rows = save("anomaly_hours", hours)

# ------------------------------------------------------------ 2. DAILY
days = spark.sql(f"""
    WITH daily AS (
        SELECT date, COUNT(*) AS hits
        FROM access_logs
        GROUP BY date
    ),
    baseline AS (
        SELECT AVG(hits) AS mean_hits, STDDEV_POP(hits) AS std_hits
        FROM daily
    )
    SELECT d.date, d.hits,
           ROUND(b.mean_hits, 1)                        AS normal_hits,
           ROUND(b.std_hits, 1)                         AS std_hits,
           ROUND((d.hits - b.mean_hits) / b.std_hits, 2) AS z,
           CASE WHEN d.hits > b.mean_hits THEN 'spike' ELSE 'drop' END AS kind
    FROM daily d CROSS JOIN baseline b
    WHERE ABS((d.hits - b.mean_hits) / b.std_hits) > {THRESHOLD}
    ORDER BY ABS((d.hits - b.mean_hits) / b.std_hits) DESC
""")
day_rows = save("anomaly_days", days)

spikes = sum(1 for r in hour_rows if r["kind"] == "spike")
print(f"\n[anomalies] {len(hour_rows)} anomalous hours ({spikes} spikes, "
      f"{len(hour_rows) - spikes} drops) and {len(day_rows)} anomalous day(s), "
      f"written to {REPORTS}/anomaly_hours and anomaly_days")
spark.stop()
