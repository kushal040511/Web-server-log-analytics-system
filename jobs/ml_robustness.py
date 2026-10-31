#!/usr/bin/env python3
"""
Robustness test: are the ML results stable, and is the difference between models real?

One 80/20 split can be lucky. This job repeats the whole experiment on 5 different random
splits (seeds 1-5) with the same cleaning, bias fix, features and settings as
ml_preprocess.py + ml_train.py, for the top 3 models of the main comparison:

  Factorization Machine (ours), Linear SVM, Logistic Regression

For every split it records precision, recall, F1, ROC-AUC and PR-AUC (PR-AUC is the honest
area metric when only ~3% of requests are attacks). It reports mean +/- standard deviation
over the 5 splits, and McNemar's test on each split: do two models make different mistakes
on the same test requests, or is the gap just noise?

    docker compose exec client spark-submit /workspace/jobs/ml_robustness.py      (make ml-robust)
"""
import sys, os, json, math, time, statistics
from urllib.parse import unquote_plus
from pyspark.sql import SparkSession, functions as F
from pyspark.ml import Pipeline
from pyspark.ml.feature import NGram, HashingTF, VectorAssembler, MaxAbsScaler
from pyspark.ml.classification import FMClassifier, LogisticRegression, LinearSVC
from pyspark.ml.evaluation import BinaryClassificationEvaluator

ATTACK = sys.argv[1] if len(sys.argv) > 1 else "hdfs://namenode:8020/data/attacks"
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else "/workspace/output"
SEEDS = [int(s) for s in os.environ.get("SEEDS", "1,2,3,4,5").split(",")]
N_FEATURES, NORMAL_PER_ATTACK = 1 << 12, 4
FM_ITERS = int(os.environ.get("FM_ITERS", 100))

spark = SparkSession.builder.appName("ml-robustness").getOrCreate()
spark.sparkContext.setLogLevel("ERROR")

# ------------------------------------------------ 1. same preprocessing as ml_preprocess.py
hp = (spark.read.option("header", True).option("multiLine", True).option("escape", '"')
      .csv(f"{ATTACK}/payload_full.csv")
      .select(F.col("payload").alias("text"), F.when(F.col("label") == "anom", 1).otherwise(0).alias("label")))
good = spark.read.text(f"{ATTACK}/goodqueries.txt").select(F.col("value").alias("text"), F.lit(0).alias("label"))
bad = spark.read.text(f"{ATTACK}/badqueries.txt").select(F.col("value").alias("text"), F.lit(1).alias("label"))
url_decode = F.udf(lambda t: unquote_plus(t) if t else t, "string")
data = (hp.unionByName(good).unionByName(bad)
        .withColumn("text", F.lower(F.trim(url_decode("text"))))
        .withColumn("text", F.trim(F.when(F.instr("text", "?") > 0, F.expr("substring(text, instr(text, '?') + 1)"))
                                   .otherwise(F.col("text"))))
        .filter(F.length("text") > 0)
        .groupBy("text").agg(F.max("label").alias("label"), F.min("label").alias("lo"))
        .filter("label = lo").drop("lo")
        .withColumn("id", F.monotonically_increasing_id())
        .cache())
n_data = data.count()
print(f"[robust] prepared dataset: {n_data:,} requests; seeds {SEEDS}")

# ------------------------------------------------ 2. same features as ml_train.py
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

MODELS = {
    "Factorization Machine (ours)": lambda s: FMClassifier(factorSize=8, maxIter=FM_ITERS, stepSize=0.2, regParam=1e-4, seed=s),
    "Linear SVM": lambda s: LinearSVC(maxIter=100, regParam=1e-4),
    "Logistic Regression": lambda s: LogisticRegression(maxIter=100, regParam=1e-4),
}
OURS = "Factorization Machine (ours)"
roc = BinaryClassificationEvaluator(metricName="areaUnderROC", rawPredictionCol="rawPrediction")
pr = BinaryClassificationEvaluator(metricName="areaUnderPR", rawPredictionCol="rawPrediction")


def metrics(pred):
    c = {(int(r["label"]), int(r["prediction"])): r["count"] for r in pred.groupBy("label", "prediction").count().collect()}
    tn, fp, fn, tp = c.get((0, 0), 0), c.get((0, 1), 0), c.get((1, 0), 0), c.get((1, 1), 0)
    p, r = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    return {"tn": tn, "fp": fp, "fn": fn, "tp": tp, "precision": round(p, 4), "recall": round(r, 4),
            "f1": round(2 * p * r / max(p + r, 1e-12), 4),
            "roc_auc": round(roc.evaluate(pred), 4), "pr_auc": round(pr.evaluate(pred), 4)}


def mcnemar(a, b):
    """McNemar's test with continuity correction. a/b = (id, label, prediction) of two models."""
    j = (a.select("id", "label", F.col("prediction").alias("pa"))
          .join(b.select("id", F.col("prediction").alias("pb")), "id"))
    row = j.agg(F.sum(((F.col("pa") == F.col("label")) & (F.col("pb") != F.col("label"))).cast("int")).alias("b"),
                F.sum(((F.col("pa") != F.col("label")) & (F.col("pb") == F.col("label"))).cast("int")).alias("c")).first()
    nb, nc = int(row["b"] or 0), int(row["c"] or 0)
    chi2 = (abs(nb - nc) - 1) ** 2 / (nb + nc) if nb + nc else 0.0
    return {"only_first_right": nb, "only_second_right": nc, "chi2": round(chi2, 3),
            "p_value": float(f"{math.erfc(math.sqrt(chi2 / 2)):.3g}")}   # chi-square, 1 degree of freedom


# ------------------------------------------------ 3. repeat on 5 random splits
runs, tests = [], []
for seed in SEEDS:
    train_all, test_raw = data.randomSplit([0.8, 0.2], seed=seed)
    n_att = train_all.filter("label = 1").count(); n_nor = train_all.filter("label = 0").count()
    train_raw = train_all.sampleBy("label", {0: min(1.0, NORMAL_PER_ATTACK * n_att / max(n_nor, 1)), 1: 1.0}, seed=seed)
    fm = features.fit(add_text_features(train_raw))
    train = fm.transform(add_text_features(train_raw)).select("label", "features").cache()
    test = fm.transform(add_text_features(test_raw)).select("id", "label", "features").cache()
    n_train, n_test = train.count(), test.count()
    print(f"\n##### seed {seed}: train {n_train:,} (balanced)  test {n_test:,} (real mix)")
    preds = {}
    for name, make in MODELS.items():
        t0 = time.time(); m = make(seed).fit(train); secs = round(time.time() - t0, 1)
        preds[name] = m.transform(test).select("id", "label", "prediction", "rawPrediction").cache()
        r = {"seed": seed, "model": name, "train": n_train, "test": n_test, "train_seconds": secs, **metrics(preds[name])}
        runs.append(r)
        print(f"  {name:<30} P {r['precision']:.4f}  R {r['recall']:.4f}  F1 {r['f1']:.4f}  "
              f"ROC-AUC {r['roc_auc']:.4f}  PR-AUC {r['pr_auc']:.4f}  ({secs}s)")
    for other in MODELS:
        if other != OURS:
            t = {"seed": seed, "first": OURS, "second": other, **mcnemar(preds[OURS], preds[other])}
            tests.append(t)
            print(f"  McNemar {OURS} vs {other}: only FM right {t['only_first_right']:,}, "
                  f"only {other} right {t['only_second_right']:,}, p = {t['p_value']}")
    for p in preds.values():
        p.unpersist()
    train.unpersist(); test.unpersist()

# ------------------------------------------------ 4. summary: mean +/- std over the splits
summary = []
for name in MODELS:
    rs = [r for r in runs if r["model"] == name]
    s = {"model": name, "runs": len(rs)}
    for k in ("precision", "recall", "f1", "roc_auc", "pr_auc", "train_seconds"):
        v = [r[k] for r in rs]
        s[k + "_mean"] = round(statistics.mean(v), 4)
        s[k + "_std"] = round(statistics.stdev(v), 4) if len(v) > 1 else 0.0
    summary.append(s)
summary.sort(key=lambda s: -s["f1_mean"])

print("\n=== MEAN +/- STD over", len(SEEDS), "random 80/20 splits")
print(f"    {'model':<30}{'precision':>17}{'recall':>17}{'F1':>17}{'ROC-AUC':>17}{'PR-AUC':>17}")
for s in summary:
    print(f"    {s['model']:<30}" + "".join(f"{s[k + '_mean']:>9.4f} ±{s[k + '_std']:.4f}"
                                           for k in ("precision", "recall", "f1", "roc_auc", "pr_auc")))

os.makedirs(f"{OUTPUT}/ml", exist_ok=True)
with open(f"{OUTPUT}/ml/ml_robustness.json", "w") as f:
    json.dump({"seeds": SEEDS, "dataset": n_data, "summary": summary, "runs": runs, "mcnemar": tests}, f, indent=1)
print(f"\n[robust] saved {OUTPUT}/ml/ml_robustness.json")
spark.stop()
