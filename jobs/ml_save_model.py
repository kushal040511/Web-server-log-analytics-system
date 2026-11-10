#!/usr/bin/env python3
"""
Train the deployed attack detector once more and save it to HDFS, so the app can use it.

Same data (the prepared training set), same features and the same Factorization Machine
settings and seed as jobs/ml_train.py, so the saved model is the one evaluated there
(F1 0.9669 on the 261,076 test requests). The saved PipelineModel holds every step from
the character lists to the prediction: n-grams -> hashing -> scaling -> FM.

    docker compose exec client spark-submit /workspace/jobs/ml_save_model.py      (make ml-model)

Writes hdfs:///models/fm_attack (Spark ML format) and checks it on the test set.
"""
import sys, time
from pyspark.sql import SparkSession, functions as F
from pyspark.ml import Pipeline, PipelineModel
from pyspark.ml.feature import NGram, HashingTF, VectorAssembler, MaxAbsScaler
from pyspark.ml.classification import FMClassifier

PREPARED = sys.argv[1] if len(sys.argv) > 1 else "hdfs://namenode:8020/data/attacks/prepared"
MODEL = sys.argv[2] if len(sys.argv) > 2 else "hdfs://namenode:8020/models/fm_attack"
N_FEATURES, SEED = 1 << 12, 42

spark = SparkSession.builder.appName("save-attack-model").getOrCreate()
spark.sparkContext.setLogLevel("ERROR")

SPECIAL = r"""['"<>;()=%&|`$\-\.\\/*+#!{}\[\]]"""


def add_text_features(df):   # identical to jobs/ml_train.py and app/scoring.py
    return (df.withColumn("chars", F.expr("filter(split(text, ''), c -> c != '')"))
        .withColumn("len", F.length("text").cast("double"))
        .withColumn("n_special", (F.length("text") - F.length(F.regexp_replace("text", SPECIAL, ""))).cast("double"))
        .withColumn("n_digits", (F.length("text") - F.length(F.regexp_replace("text", "[0-9]", ""))).cast("double"))
        .withColumn("n_keywords", F.lit(0.0)
            + F.col("text").rlike(r"select|union|insert|drop|update|delete|sleep\(|benchmark").cast("double")
            + F.col("text").rlike(r"<script|javascript:|onerror|onload|alert\(").cast("double")
            + F.col("text").rlike(r"\.\./|/etc/passwd|win\.ini|boot\.ini").cast("double")
            + F.col("text").rlike(r";\s*(ls|cat|id|uname|wget|curl)|\|\s*(ls|cat|id)|`").cast("double")))


pipeline = Pipeline(stages=[
    NGram(n=2, inputCol="chars", outputCol="bi"), NGram(n=3, inputCol="chars", outputCol="tri"),
    HashingTF(inputCol="chars", outputCol="features_1", numFeatures=N_FEATURES),
    HashingTF(inputCol="bi", outputCol="features_2", numFeatures=N_FEATURES),
    HashingTF(inputCol="tri", outputCol="features_3", numFeatures=N_FEATURES),
    VectorAssembler(inputCols=["features_1", "features_2", "features_3", "len", "n_special", "n_digits", "n_keywords"],
                    outputCol="features_raw"),
    MaxAbsScaler(inputCol="features_raw", outputCol="features"),
    FMClassifier(factorSize=8, maxIter=100, stepSize=0.2, regParam=1e-4, seed=SEED)])

train = add_text_features(spark.read.parquet(f"{PREPARED}/train")).cache()
t0 = time.time()
model = pipeline.fit(train)
print(f"[model] Factorization Machine trained on {train.count():,} requests in {time.time() - t0:.0f}s")
model.write().overwrite().save(MODEL)
print(f"[model] saved to {MODEL}")

# check: load it back and score the test set
pred = PipelineModel.load(MODEL).transform(add_text_features(spark.read.parquet(f"{PREPARED}/test")))
c = {(int(r["label"]), int(r["prediction"])): r["count"] for r in pred.groupBy("label", "prediction").count().collect()}
tp, fp, fn = c.get((1, 1), 0), c.get((0, 1), 0), c.get((1, 0), 0)
p, r = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
print(f"[model] reloaded model on the test set: precision {p:.4f}  recall {r:.4f}  F1 {2 * p * r / max(p + r, 1e-12):.4f}")
spark.stop()
