#!/usr/bin/env python3
"""
Stage 3 — turn the HDFS report tables into what the website serves.

Writes three things into the mounted ./output directory:

  dashboard.json      the summary every page renders from (a few hundred rows)
  tables/*.csv        the full report tables, for the download buttons
  tables/*.json       the same, as JSON

The report tables in HDFS stay the source of truth. This job only gathers what
a browser actually needs, so the web container never has to talk to HDFS — which
keeps it small and means the site still works with the cluster stopped.

Two cross-tabs (hour x date, status x date) are exported so the site can
recompute the hour-of-day and status charts for any date range the user picks,
rather than being stuck with period-wide totals.

    docker compose exec client spark-submit /workspace/jobs/export_dashboard.py
"""
import sys, os, json, csv
from datetime import datetime, timezone
from pyspark.sql import SparkSession, functions as F

REPORTS = sys.argv[1] if len(sys.argv) > 1 else "hdfs://namenode:8020/data/weblogs/reports"
CURATED = sys.argv[2] if len(sys.argv) > 2 else "hdfs://namenode:8020/data/weblogs/curated"
OUT_DIR = sys.argv[3] if len(sys.argv) > 3 else "/workspace/output"

spark = SparkSession.builder.appName("weblog-export-dashboard").getOrCreate()
spark.sparkContext.setLogLevel("WARN")

TABLE_DIR = os.path.join(OUT_DIR, "tables")
os.makedirs(TABLE_DIR, exist_ok=True)


def clean(v):
    """Decimal and Date aren't JSON-serialisable; make them so."""
    return float(v) if hasattr(v, "as_tuple") else v


def rows_of(df):
    return [{k: clean(v) for k, v in r.asDict().items()} for r in df.collect()]


def table(name, limit=None, order=None, dump=True):
    """Read a report table; optionally write the FULL table for download."""
    try:
        df = spark.read.parquet(f"{REPORTS}/{name}")
    except Exception as e:                    # a missing table shouldn't kill the export
        print(f"[export] skipping {name}: {type(e).__name__}")
        return []
    if order is not None:              # a column name or a Column expression
        df = df.orderBy(order)
    full = rows_of(df)
    if dump and full:
        cols = list(full[0].keys())
        with open(os.path.join(TABLE_DIR, f"{name}.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(full)
        with open(os.path.join(TABLE_DIR, f"{name}.json"), "w") as fh:
            json.dump(full, fh, indent=1, default=str)
    return full[:limit] if limit else full


logs = spark.read.parquet(CURATED)
agg = logs.agg(
    F.count("*").alias("records"),
    F.countDistinct("host").alias("visitors"),
    F.countDistinct("endpoint").alias("endpoints"),
    F.sum("bytes").alias("bytes"),
    F.min("date").alias("date_min"),
    F.max("date").alias("date_max"),
    F.countDistinct("date").alias("days"),
).first().asDict()

status = table("status_distribution")
err_hits = sum(r["hits"] for r in status if r.get("status", 0) >= 400)
total = agg["records"] or 1

# The top-N report tables are capped, so counting their rows would report the cap
# rather than the truth ("300 distinct broken URLs" when 300 is just the LIMIT).
# These two come from the full curated data and are what the site quotes.
nf = logs.where("status = 404")
nf_agg = nf.agg(F.count("*").alias("hits"),
                F.countDistinct("endpoint").alias("urls")).first().asDict()

# ---- cross-tabs that make the date-range drill-down real -------------------
# Without these, picking a date range could only filter the daily series; the
# hour-of-day and status charts would still show period-wide totals.
hour_by_date = rows_of(
    logs.groupBy("date", "hour").agg(F.count("*").alias("hits")).orderBy("date", "hour"))
status_by_date = rows_of(
    logs.groupBy("date", "status").agg(F.count("*").alias("hits")).orderBy("date", "status"))

data = {
    "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    "meta": {
        "records": agg["records"],
        "visitors": agg["visitors"],
        "endpoints": agg["endpoints"],
        "bytes": agg["bytes"],
        "gb": round((agg["bytes"] or 0) / 1024 ** 3, 2),
        "days": agg["days"],
        "date_min": str(agg["date_min"]),
        "date_max": str(agg["date_max"]),
        "error_pct": round(100.0 * err_hits / total, 3),
        "not_found_total": nf_agg["hits"],
        "not_found_urls": nf_agg["urls"],
    },
    # How far the capped tables were cut, so the site can say "top 300 of 1,842"
    # instead of implying it is showing everything.
    "limits": {"top_urls": 300, "broken_links": 300, "heaviest_hosts": 300,
               "content_types": 40},
    "by_hour":          table("by_hour", order="hour"),
    "daily":            table("daily", order="date"),
    "errors_daily":     table("errors_daily", order="date"),
    "weekday":          table("by_weekday"),
    "status":           status,
    "top_urls":         table("top_urls", limit=300),
    "broken_links":     table("broken_links", limit=300),
    "content_types":    table("by_content_type", limit=40),
    "size_buckets":     table("size_buckets"),
    "size_percentiles": (table("size_percentiles") or [{}])[0],
    "top_hosts":        table("heaviest_hosts", limit=300),
    # Z-score anomaly detection (jobs/anomalies.py); empty lists until `make anomalies` has run
    "anomaly_hours":    table("anomaly_hours", order=F.abs(F.col("z")).desc()),
    "anomaly_days":     table("anomaly_days", order=F.abs(F.col("z")).desc()),
    "hour_by_date":     hour_by_date,
    "status_by_date":   status_by_date,
    "bots": None,
}

# The bot-detection summary is optional — the site hides that page's content
# with a clear message until `make bots` has run.
try:
    raw = spark.read.text(f"{REPORTS}/bot_detection_summary").collect()
    data["bots"] = json.loads("".join(r["value"] for r in raw))
except Exception as e:
    print(f"[export] no bot-detection summary yet ({type(e).__name__}) — run `make bots` first")

out_json = os.path.join(OUT_DIR, "dashboard.json")
with open(out_json, "w") as fh:
    json.dump(data, fh, indent=1, default=str)

tables = sorted(f for f in os.listdir(TABLE_DIR) if f.endswith(".csv"))
print(f"[export] wrote {out_json}  ({os.path.getsize(out_json)/1024:.0f} KB)")
print(f"[export] wrote {len(tables)} downloadable tables to {TABLE_DIR}")
print(f"[export] {data['meta']['records']:,} records · {data['meta']['visitors']:,} visitors "
      f"· {data['meta']['gb']} GB · {data['meta']['days']} days")
spark.stop()
