#!/usr/bin/env python3
"""
ML preprocessing: labelled HTTP requests -> clean, de-duplicated, labelled, split data.

Every step prints how many rows went in and out, so the preprocessing is visible.

  1. Load      HttpParams (payload_full.csv) + Fwaf (goodqueries.txt, badqueries.txt)
  2. Label     binary target: 0 = normal, 1 = attack (sqli, xss, cmdi, path-traversal)
  3. Clean     URL-decode (%27 -> '), lower-case, trim spaces, drop empty rows
  3b. Bias fix keep only the query part of URLs that have one (removes the "?" shortcut)
  4. De-dup    one row per distinct request
  5. Conflict  drop requests that appear with BOTH labels
  6. Inspect   class balance, length statistics, share of requests containing '?'
  7. Split     80% train / 20% test (seed 42); train balanced to 4 normal : 1 attack,
               test keeps the real class mix
  8. Save      Parquet to /data/attacks/prepared/{train,test}

    docker compose exec client spark-submit /workspace/jobs/ml_preprocess.py
"""
import sys
from pyspark.sql import SparkSession, functions as F

ATTACK = sys.argv[1] if len(sys.argv) > 1 else "hdfs://namenode:8020/data/attacks"
OUT    = sys.argv[2] if len(sys.argv) > 2 else "hdfs://namenode:8020/data/attacks/prepared"
SEED, NORMAL_PER_ATTACK = 42, 4

spark = SparkSession.builder.appName("ml-preprocess").getOrCreate()
spark.sparkContext.setLogLevel("ERROR")

def step(title):
    print(f"\n=== {title}")

# 1. LOAD + 2. LABEL
step("1-2. Load and label")
hp = (spark.read.option("header", True).option("multiLine", True).option("escape", '"')
      .csv(f"{ATTACK}/payload_full.csv")
      .select(F.col("payload").alias("text"),
              F.when(F.col("label") == "anom", 1).otherwise(0).alias("label"),
              F.col("attack_type"), F.lit("HttpParams").alias("source")))
good = spark.read.text(f"{ATTACK}/goodqueries.txt").select(
    F.col("value").alias("text"), F.lit(0).alias("label"), F.lit("norm").alias("attack_type"), F.lit("Fwaf").alias("source"))
bad = spark.read.text(f"{ATTACK}/badqueries.txt").select(
    F.col("value").alias("text"), F.lit(1).alias("label"), F.lit("attack").alias("attack_type"), F.lit("Fwaf").alias("source"))
raw = hp.unionByName(good).unionByName(bad).cache()
n_raw = raw.count()
raw.groupBy("source", "attack_type").count().orderBy("source", F.desc("count")).show()
print(f"rows loaded: {n_raw:,}")
print("examples before cleaning:")
raw.filter(F.col("text").contains("%")).select("text", "label").show(3, truncate=90)

# 3. CLEAN
step("3. Clean: URL-decode, lower-case, trim, drop empty")
from urllib.parse import unquote_plus
url_decode = F.udf(lambda t: unquote_plus(t) if t else t, "string")   # never fails on bad %
clean = (raw.withColumn("text", F.lower(F.trim(url_decode("text"))))
         .filter(F.length("text") > 0))
n_clean = clean.count()
print(f"rows after cleaning: {n_clean:,}   (empty rows removed: {n_raw - n_clean:,})")
clean.filter(F.col("text").contains("'")).select("text", "label").show(3, truncate=90)

# 3b. BIAS FIX: keep only the request content
# Normal Fwaf URLs rarely have a query string but attacks usually do, so a model can learn
# the shortcut "contains ? = attack". Keeping only the part after '?' removes that shortcut:
# the model must judge what is actually being sent.
step("3b. Bias fix: keep only the query part of URLs that have one")
(clean.groupBy("label").agg(F.round(100 * F.avg(F.col("text").contains("?").cast("int")), 1)
                            .alias("pct_with_?_before")).orderBy("label").show())
clean = clean.withColumn("text", F.trim(F.when(F.instr("text", "?") > 0,
                                               F.expr("substring(text, instr(text, '?') + 1)"))
                                         .otherwise(F.col("text")))).filter(F.length("text") > 0)
print("after: '/top.php?stuff=1 or 1=1' -> 'stuff=1 or 1=1'")
n_clean = clean.count()

# 4. DE-DUPLICATE + 5. CONFLICTS
step("4-5. Remove duplicates and conflicting labels")
grouped = clean.groupBy("text").agg(F.max("label").alias("label"), F.min("label").alias("lo"),
                                    F.first("source").alias("source"))
n_distinct = grouped.count()
conflicts = grouped.filter(F.col("label") != F.col("lo")).count()
data = grouped.filter(F.col("label") == F.col("lo")).drop("lo").cache()
n_final = data.count()
print(f"distinct requests: {n_distinct:,}   (duplicates removed: {n_clean - n_distinct:,})")
print(f"conflicting labels removed: {conflicts:,}")
print(f"final dataset: {n_final:,} requests")

# 6. INSPECT
step("6. Inspect the prepared data")
(data.groupBy("label").agg(F.count("*").alias("requests"),
                           F.round(100 * F.count("*") / n_final, 2).alias("pct"),
                           F.round(F.avg(F.length("text")), 1).alias("avg_length"),
                           F.expr("percentile_approx(length(text), 0.5)").alias("median_length"),
                           F.round(100 * F.avg(F.col("text").contains("?").cast("int")), 1).alias("pct_with_?"))
     .withColumn("class", F.when(F.col("label") == 1, "attack").otherwise("normal"))
     .select("label", "class", "requests", "pct", "avg_length", "median_length", "pct_with_?").orderBy("label").show())

# 7. SPLIT + BALANCE
step("7. Split 80/20, balance the training set")
train_all, test = data.randomSplit([0.8, 0.2], seed=SEED)
n_att = train_all.filter("label = 1").count()
n_nor = train_all.filter("label = 0").count()
frac = min(1.0, NORMAL_PER_ATTACK * n_att / max(n_nor, 1))
train = train_all.sampleBy("label", {0: frac, 1: 1.0}, seed=SEED)
for name, df in [("train (balanced)", train), ("test (real mix)", test)]:
    c = {r["label"]: r["count"] for r in df.groupBy("label").count().collect()}
    print(f"{name:<18} normal {c.get(0, 0):>9,}   attack {c.get(1, 0):>7,}   total {sum(c.values()):>9,}")

# 8. SAVE
train.write.mode("overwrite").parquet(f"{OUT}/train")
test.write.mode("overwrite").parquet(f"{OUT}/test")
print(f"\n[preprocess] saved to {OUT}/train and {OUT}/test")
spark.stop()
