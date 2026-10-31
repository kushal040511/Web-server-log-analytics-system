#!/usr/bin/env python3
"""
Hyper-parameter check on a validation set (never on the test set).

The training set written by ml_preprocess.py is split again 80/20 into a fitting part and a
validation part. Every setting below is trained on the fitting part and scored on the
validation part; the test set is not read at all. Each model gets a small grid of the
setting that matters most for it, so no model is favoured by extra tuning:

  Factorization Machine   factorSize {4, 8, 16} x stepSize {0.05, 0.2}   (maxIter 100)
  Linear SVM              regParam {1e-5, 1e-4, 1e-3}
  Logistic Regression     regParam {1e-5, 1e-4, 1e-3}

    docker compose exec client spark-submit /workspace/jobs/ml_tune.py       (make ml-tune)
"""
import sys, os, json, time, itertools
from pyspark.sql import SparkSession, functions as F
from pyspark.ml import Pipeline
from pyspark.ml.feature import NGram, HashingTF, VectorAssembler, MaxAbsScaler
from pyspark.ml.classification import FMClassifier, LogisticRegression, LinearSVC

PREPARED = sys.argv[1] if len(sys.argv) > 1 else "hdfs://namenode:8020/data/attacks/prepared"
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else "/workspace/output"
SEED, N_FEATURES = 42, 1 << 12

spark = SparkSession.builder.appName("ml-tune").getOrCreate()
spark.sparkContext.setLogLevel("ERROR")

SPECIAL = r"""['"<>;()=%&|`$\-\.\\/*+#!{}\[\]]"""


def add_text_features(df):   # same as ml_train.py
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

train_all = spark.read.parquet(f"{PREPARED}/train")
fit_raw, val_raw = train_all.randomSplit([0.8, 0.2], seed=SEED)
fm = features.fit(add_text_features(fit_raw))
fit = fm.transform(add_text_features(fit_raw)).select("label", "features").cache()
val = fm.transform(add_text_features(val_raw)).select("label", "features").cache()
print(f"[tune] fitting part {fit.count():,}   validation part {val.count():,}   (test set not used)")


def f1(pred):
    c = {(int(r["label"]), int(r["prediction"])): r["count"] for r in pred.groupBy("label", "prediction").count().collect()}
    tp, fp, fn = c.get((1, 1), 0), c.get((0, 1), 0), c.get((1, 0), 0)
    p, r = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    return round(2 * p * r / max(p + r, 1e-12), 4)


GRID = ([("Factorization Machine (ours)", {"factorSize": k, "stepSize": s},
          lambda k=k, s=s: FMClassifier(factorSize=k, stepSize=s, maxIter=100, regParam=1e-4, seed=SEED))
         for k, s in itertools.product([4, 8, 16], [0.05, 0.2])]
        + [("Linear SVM", {"regParam": r}, lambda r=r: LinearSVC(maxIter=100, regParam=r)) for r in (1e-5, 1e-4, 1e-3)]
        + [("Logistic Regression", {"regParam": r}, lambda r=r: LogisticRegression(maxIter=100, regParam=r))
           for r in (1e-5, 1e-4, 1e-3)])

rows = []
for model, params, make in GRID:
    t0 = time.time(); m = make().fit(fit)
    row = {"model": model, "params": params, "val_f1": f1(m.transform(val)), "train_seconds": round(time.time() - t0, 1)}
    rows.append(row)
    print(f"  {model:<30} {json.dumps(params):<40} validation F1 {row['val_f1']:.4f}  ({row['train_seconds']}s)", flush=True)

best = {}
for r in rows:
    if r["model"] not in best or r["val_f1"] > best[r["model"]]["val_f1"]:
        best[r["model"]] = r
print("\n=== best setting per model (validation F1)")
for m, r in best.items():
    print(f"  {m:<30} {json.dumps(r['params']):<40} {r['val_f1']:.4f}")

os.makedirs(f"{OUTPUT}/ml", exist_ok=True)
with open(f"{OUTPUT}/ml/ml_tuning.json", "w") as f:
    json.dump({"fit": fit.count(), "validation": val.count(), "grid": rows, "best": best}, f, indent=1)
print(f"[tune] saved {OUTPUT}/ml/ml_tuning.json")
spark.stop()
