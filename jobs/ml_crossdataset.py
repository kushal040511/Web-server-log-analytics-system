#!/usr/bin/env python3
"""
Cross-dataset test: does a model trained on one labelled dataset work on a different one?

  Experiment A: train on Fwaf (URLs)            -> test on HttpParams (parameter values)
  Experiment B: train on HttpParams             -> test on Fwaf
  Baseline    : mixed 80/20 split (ml_train.py) for comparison

Same cleaning, bias fix, features and models as ml_preprocess.py / ml_train.py.
Models: Factorization Machine (ours), Linear SVM, Logistic Regression (the top 3).

    docker compose exec client spark-submit /workspace/jobs/ml_crossdataset.py
"""
import sys, os, json, time
from urllib.parse import unquote_plus
from pyspark.sql import SparkSession, functions as F
from pyspark.ml import Pipeline
from pyspark.ml.feature import NGram, HashingTF, VectorAssembler, MaxAbsScaler
from pyspark.ml.classification import FMClassifier, LogisticRegression, LinearSVC

ATTACK = sys.argv[1] if len(sys.argv) > 1 else "hdfs://namenode:8020/data/attacks"
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else "/workspace/output"
N_FEATURES, SEED, NORMAL_PER_ATTACK = 1 << 12, 42, 4

spark = SparkSession.builder.appName("ml-cross-dataset").getOrCreate()
spark.sparkContext.setLogLevel("ERROR")

# ---------------------------------------------------- load + clean (as ml_preprocess.py)
hp = (spark.read.option("header", True).option("multiLine", True).option("escape", '"')
      .csv(f"{ATTACK}/payload_full.csv")
      .select(F.col("payload").alias("text"),
              F.when(F.col("label") == "anom", 1).otherwise(0).alias("label"), F.lit("HttpParams").alias("source")))
good = spark.read.text(f"{ATTACK}/goodqueries.txt").select(F.col("value").alias("text"), F.lit(0).alias("label"), F.lit("Fwaf").alias("source"))
bad = spark.read.text(f"{ATTACK}/badqueries.txt").select(F.col("value").alias("text"), F.lit(1).alias("label"), F.lit("Fwaf").alias("source"))
url_decode = F.udf(lambda t: unquote_plus(t) if t else t, "string")
data = (hp.unionByName(good).unionByName(bad)
        .withColumn("text", F.lower(F.trim(url_decode("text"))))
        .withColumn("text", F.trim(F.when(F.instr("text", "?") > 0, F.expr("substring(text, instr(text, '?') + 1)"))
                                   .otherwise(F.col("text"))))
        .filter(F.length("text") > 0)
        .groupBy("source", "text").agg(F.max("label").alias("label"), F.min("label").alias("lo"))
        .filter("label = lo").drop("lo").cache())

# ---------------------------------------------------- features (as ml_train.py)
SPECIAL = r"""['"<>;()=%&|`$\-\.\\/*+#!{}\[\]]"""
def add_text_features(df):
    return (df.withColumn("chars", F.expr("filter(split(text, ''), c -> c != '')"))
        .withColumn("len", F.length("text").cast("double"))
        .withColumn("n_special", (F.length("text") - F.length(F.regexp_replace("text", SPECIAL, ""))).cast("double"))
        .withColumn("n_digits", (F.length("text") - F.length(F.regexp_replace("text", "[0-9]", ""))).cast("double"))
        .withColumn("n_keywords", F.lit(0.0)
            + F.col("text").rlike(r"select|union|insert|drop|update|delete|sleep\(|benchmark").cast("double")
            + F.col("text").rlike(r"<script|javascript:|onerror|onload|alert\(").cast("double")
            + F.col("text").rlike(r"\.\./|/etc/passwd|win\.ini|boot\.ini").cast("double")
            + F.col("text").rlike(r";\s*(ls|cat|id|uname|wget|curl)|\|\s*(ls|cat|id)|`").cast("double")))

features = Pipeline(stages=[
    NGram(n=2, inputCol="chars", outputCol="bi"), NGram(n=3, inputCol="chars", outputCol="tri"),
    HashingTF(inputCol="chars", outputCol="tf1", numFeatures=N_FEATURES),
    HashingTF(inputCol="bi", outputCol="tf2", numFeatures=N_FEATURES),
    HashingTF(inputCol="tri", outputCol="tf3", numFeatures=N_FEATURES),
    VectorAssembler(inputCols=["tf1", "tf2", "tf3", "len", "n_special", "n_digits", "n_keywords"], outputCol="raw_features"),
    MaxAbsScaler(inputCol="raw_features", outputCol="features")])

def balance(df):
    n_att = df.filter("label = 1").count(); n_nor = df.filter("label = 0").count()
    return df.sampleBy("label", {0: min(1.0, NORMAL_PER_ATTACK * n_att / max(n_nor, 1)), 1: 1.0}, seed=SEED)

def evaluate(pred):
    c = {(int(r["label"]), int(r["prediction"])): r["count"] for r in pred.groupBy("label", "prediction").count().collect()}
    tn, fp, fn, tp = c.get((0, 0), 0), c.get((0, 1), 0), c.get((1, 0), 0), c.get((1, 1), 0)
    p = tp / max(tp + fp, 1); r = tp / max(tp + fn, 1)
    return {"tn": tn, "fp": fp, "fn": fn, "tp": tp, "accuracy": round((tp + tn) / max(tp + tn + fp + fn, 1), 4),
            "precision": round(p, 4), "recall": round(r, 4), "f1": round(2 * p * r / max(p + r, 1e-12), 4)}

models = {"Factorization Machine (ours)": lambda: FMClassifier(factorSize=8, maxIter=100, stepSize=0.2, regParam=1e-4, seed=SEED),
          "Linear SVM": lambda: LinearSVC(maxIter=100, regParam=1e-4),
          "Logistic Regression": lambda: LogisticRegression(maxIter=100, regParam=1e-4)}

results = []
for name, train_src, test_src in [("A: train Fwaf -> test HttpParams", "Fwaf", "HttpParams"),
                                  ("B: train HttpParams -> test Fwaf", "HttpParams", "Fwaf")]:
    train_raw = add_text_features(balance(data.filter(F.col("source") == train_src)))
    test_raw = add_text_features(data.filter(F.col("source") == test_src))
    fm = features.fit(train_raw)
    train = fm.transform(train_raw).select("label", "features").cache()
    test = fm.transform(test_raw).select("label", "features").cache()
    print(f"\n##### {name}: train {train.count():,}  test {test.count():,}")
    for mname, make in models.items():
        t0 = time.time(); m = make().fit(train)
        r = {"experiment": name, "model": mname, "train_seconds": round(time.time() - t0, 1), **evaluate(m.transform(test))}
        results.append(r)
        print(f"  {mname:<30} precision {r['precision']:.4f}  recall {r['recall']:.4f}  F1 {r['f1']:.4f}   "
              f"(TN {r['tn']:,} FP {r['fp']:,} FN {r['fn']:,} TP {r['tp']:,})")
    train.unpersist(); test.unpersist()

os.makedirs(f"{OUTPUT}/ml", exist_ok=True)
with open(f"{OUTPUT}/ml/ml_crossdataset.json", "w") as f:
    json.dump(results, f, indent=1)
print(f"\n[ml] saved {OUTPUT}/ml/ml_crossdataset.json")
spark.stop()
