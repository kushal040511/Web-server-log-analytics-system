# Kushal — Apache Spark: ETL into Parquet

My big data tool is **Apache Spark** (PySpark). I built the processing step: a Spark job that
reads the raw log text from Kriti's HDFS raw zone and turns it into 3,461,612 clean, typed
records, stored as Parquet and split by date. Gargi's Spark SQL reports run on this data.

| My file | What it does |
|---|---|
| `jobs/etl.py` | Reads the raw logs from HDFS; regex-parses each Common Log Format line into host, time, method, URL, protocol, status and bytes; converts the types; keeps bad lines aside as rejects; adds date, hour, day of week and file extension; writes Snappy Parquet partitioned by date |
| `conf/spark-defaults.conf` | Spark master URL, 2 GB memory, 16 shuffle partitions, New York time zone |

## What etl.py does to the data

| | Before (HDFS raw zone) | After (HDFS curated zone) |
|---|---|---|
| Shape | One text column, one row per line | 11 typed columns per row |
| Count | 3,461,613 lines | 3,461,612 rows + 1 reject |
| Storage | 2 plain text files, 356 MB | Snappy Parquet, 58 date folders, 41.5 MB |

The one reject is a truncated line in the July file that reads only `alyssa.p`. It is copied to
`curated_rejects`, not deleted.

## Demo (about 3 minutes)

1. `make etl` — the job prints raw lines, clean records, rejects and the parse rate.
2. `make ls` — show the curated Parquet folders, one per date.
3. `docker compose exec client pyspark`, then
   `df = spark.read.parquet("/data/weblogs/curated"); df.printSchema(); df.show(10); df.count()`
   to show the clean, typed table.
4. Open http://localhost:8080 — the Spark master shows the completed job.

## What to say

- Raw stays untouched; curated can always be rebuilt from it — fix the regex, rerun, nothing is lost.
- A bad line is never silently dropped: it goes to `curated_rejects`, and the parse rate is printed every run.
- Partitioning by date means a one-day query reads one folder, not all 58 days.
- Parquet stores data by column, so a report reads only the columns it needs.
- The data covers 1 July – 31 August 1995; 29–31 July and 2 August are missing from the source.

## Likely questions

- Transformation vs action? — `select`, `filter`, `withColumn` only build a plan; `count`, `show` and `write` run it (lazy evaluation).
- Why Parquet? — Column-based and compressed: 356 MB of text becomes 41.5 MB.
- Why regex? — Every line follows the same Common Log Format, so one pattern per field cuts it reliably.

Hand over: "The data is now clean and typed. Gargi will show what Spark SQL finds in it."
