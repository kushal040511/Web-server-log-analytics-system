#!/usr/bin/env python3
"""
Stage 1 of the pipeline — raw access logs to a curated Parquet layer.

  hdfs:///data/weblogs/raw/          plain text, immutable, never edited
        |
        |  regex parse -> type cast -> drop malformed -> derive date/hour/ext
        v
  hdfs:///data/weblogs/curated/      Parquet, partitioned by date, Snappy

Run it:
    docker compose exec client spark-submit /workspace/jobs/etl.py

The raw zone is the audit trail. If this job has a bug, fix the regex and run
it again — nothing is lost, because raw is never overwritten.
"""
import sys, json
from pyspark.sql import SparkSession, functions as F
from pyspark.sql.types import IntegerType, LongType

RAW = sys.argv[1] if len(sys.argv) > 1 else "hdfs://namenode:8020/data/weblogs/raw"
CURATED = sys.argv[2] if len(sys.argv) > 2 else "hdfs://namenode:8020/data/weblogs/curated"

spark = SparkSession.builder.appName("weblog-etl").getOrCreate()
spark.sparkContext.setLogLevel("WARN")

# ----------------------------------------------------------------- 1. INGEST
raw = spark.read.text(RAW)
total = raw.count()
print(f"[etl] read {total:,} raw lines from {RAW}")

# ------------------------------------------------------------------ 2. PARSE
# NCSA Common Log Format:
#   host - - [10/Oct/2000:13:55:36 -0700] "GET /page.html HTTP/1.0" 200 2326
HOST   = r'^(\S+)'
TS     = r'\[(\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2} [+\-]\d{4})\]'
REQ    = r'"(\S+)\s(\S+)\s*(\S*)"'
STATUS = r'\s(\d{3})\s'
BYTES  = r'\s(\d+|-)$'

parsed = (raw
    .select(
        # the original line rides along so a reject can be inspected as written
        F.col("value").alias("raw"),
        F.regexp_extract("value", HOST, 1).alias("host"),
        F.regexp_extract("value", TS, 1).alias("ts_str"),
        F.regexp_extract("value", REQ, 1).alias("method"),
        F.regexp_extract("value", REQ, 2).alias("endpoint"),
        F.regexp_extract("value", REQ, 3).alias("protocol"),
        F.regexp_extract("value", STATUS, 1).cast(IntegerType()).alias("status"),
        F.regexp_extract("value", BYTES, 1).alias("bytes_str"))
    # a dash in the size field means "no body", which is zero bytes, not null
    .withColumn("bytes", F.when(F.col("bytes_str") == "-", F.lit(0))
                          .otherwise(F.col("bytes_str").cast(LongType())))
    .withColumn("ts", F.to_timestamp("ts_str", "dd/MMM/yyyy:HH:mm:ss Z"))
    .drop("ts_str", "bytes_str"))

# ---------------------------------------------------- 3. CLEAN + DERIVE
bad = parsed.filter(F.col("ts").isNull() | F.col("status").isNull() | (F.col("host") == ""))
n_bad = bad.count()

clean = (parsed
    .filter(F.col("ts").isNotNull() & F.col("status").isNotNull() & (F.col("host") != ""))
    .drop("raw")
    .withColumn("date", F.to_date("ts"))
    .withColumn("hour", F.hour("ts"))
    .withColumn("dow",  F.date_format("ts", "EEEE"))
    .withColumn("ext",  F.lower(F.regexp_extract("endpoint", r"\.([A-Za-z0-9]{1,5})$", 1))))

n_clean = clean.count()
rate = 100.0 * n_clean / total if total else 0.0

# Silently dropping bad rows is how you end up trusting wrong numbers, so the
# parse rate is reported on every run and the rejects are kept for inspection.
if n_bad:
    # the text writer takes exactly one string column, so write the raw line
    bad.select("raw").limit(1000).write.mode("overwrite").text(CURATED.rstrip("/") + "_rejects")

# ------------------------------------------------------------- 4. WRITE
(clean.write
      .mode("overwrite")
      .option("compression", "snappy")
      .partitionBy("date")
      .parquet(CURATED))

summary = {
    "raw_lines": total,
    "clean_records": n_clean,
    "malformed": n_bad,
    "parse_rate_pct": round(rate, 3),
    "date_min": str(clean.agg(F.min("date")).first()[0]),
    "date_max": str(clean.agg(F.max("date")).first()[0]),
    "output": CURATED,
}
print("[etl] " + json.dumps(summary, indent=2))

if rate < 99.0:
    print(f"[etl] WARNING: parse rate {rate:.2f}% is below 99% — the log format "
          f"may have changed. Check {CURATED}_rejects")

spark.stop()
