#!/usr/bin/env python3
"""
Stage 4 of the pipeline: web-attack detection with a Factorization Machine (Spark MLlib).

Task     supervised binary classification, 2 classes: 0 = normal request, 1 = attack
Input    the prepared train / test sets written by jobs/ml_preprocess.py
Model    FMClassifier (Factorization Machine): logistic regression PLUS a learned weight
         for every PAIR of features, e.g. "'" together with "or" together with "--".
Compared with 7 Spark MLlib models on the same features and the same test set:
         Logistic Regression, Linear SVM, Naive Bayes, Decision Tree, Random Forest,
         Gradient Boosted Trees, Multilayer Perceptron (neural network)
Output   confusion matrix + accuracy, precision, recall, F1 for every model;
         then the FM model labels every distinct URL in the NASA logs.
           HDFS     reports/ml_comparison, reports/ml_flagged_urls
           ./output ml/ml_results.json and ml/ml_report.html (open in any browser)

    docker compose exec client spark-submit /workspace/jobs/ml_train.py
    ONLY="Factorization,Logistic" ...   run just some of the models
"""
import sys, os, json, time, html
from pyspark.sql import SparkSession, functions as F
from pyspark.ml import Pipeline
from pyspark.ml.functions import vector_to_array
from pyspark.ml.feature import NGram, HashingTF, VectorAssembler, MaxAbsScaler
from pyspark.ml.classification import (FMClassifier, LogisticRegression, LinearSVC, NaiveBayes,
                                       DecisionTreeClassifier, RandomForestClassifier,
                                       GBTClassifier, MultilayerPerceptronClassifier)

PREPARED = sys.argv[1] if len(sys.argv) > 1 else "hdfs://namenode:8020/data/attacks/prepared"
CURATED  = sys.argv[2] if len(sys.argv) > 2 else "hdfs://namenode:8020/data/weblogs/curated"
REPORTS  = sys.argv[3] if len(sys.argv) > 3 else "hdfs://namenode:8020/data/weblogs/reports"
OUTPUT   = sys.argv[4] if len(sys.argv) > 4 else "/workspace/output"
N_FEATURES = 1 << 12           # hashed buckets per n-gram size (1, 2 and 3 characters)
SEED = 42
ONLY = os.environ.get("ONLY")

spark = SparkSession.builder.appName("weblog-attack-classifier").getOrCreate()
spark.sparkContext.setLogLevel("ERROR")

# ------------------------------------------------------------- 1. FEATURES
SPECIAL = r"""['"<>;()=%&|`$\-\.\\/*+#!{}\[\]]"""

def add_text_features(df):
    """Character list + 4 hand-made numbers for every request in column `text`."""
    return (df
        .withColumn("chars", F.expr("filter(split(text, ''), c -> c != '')"))
        .withColumn("len", F.length("text").cast("double"))
        .withColumn("n_special", (F.length("text") - F.length(F.regexp_replace("text", SPECIAL, ""))).cast("double"))
        .withColumn("n_digits", (F.length("text") - F.length(F.regexp_replace("text", "[0-9]", ""))).cast("double"))
        .withColumn("n_keywords", F.lit(0.0)
            + F.col("text").rlike(r"select|union|insert|drop|update|delete|sleep\(|benchmark").cast("double")
            + F.col("text").rlike(r"<script|javascript:|onerror|onload|alert\(").cast("double")
            + F.col("text").rlike(r"\.\./|/etc/passwd|win\.ini|boot\.ini").cast("double")
            + F.col("text").rlike(r";\s*(ls|cat|id|uname|wget|curl)|\|\s*(ls|cat|id)|`").cast("double")))

def feature_pipeline(n, out):
    """Character 1-, 2-, 3-grams hashed into n buckets each, plus the 4 counts, scaled to 0..1."""
    return [
        HashingTF(inputCol="chars", outputCol=f"{out}_1", numFeatures=n),
        HashingTF(inputCol="bi",    outputCol=f"{out}_2", numFeatures=n),
        HashingTF(inputCol="tri",   outputCol=f"{out}_3", numFeatures=n),
        VectorAssembler(inputCols=[f"{out}_1", f"{out}_2", f"{out}_3", "len", "n_special", "n_digits",
                                   "n_keywords"], outputCol=f"{out}_raw"),
        MaxAbsScaler(inputCol=f"{out}_raw", outputCol=out)]   # 0..1, keeps vectors sparse

# Spark's neural network (MLP) turns its input into DENSE blocks, so the full 12,292 features
# would need ~15 GB. It gets a compact 388-feature version of the same n-grams instead.
N_SMALL = 1 << 7
features = Pipeline(stages=[NGram(n=2, inputCol="chars", outputCol="bi"),
                            NGram(n=3, inputCol="chars", outputCol="tri")]
                    + feature_pipeline(N_FEATURES, "features") + feature_pipeline(N_SMALL, "features_small"))
N_ALL, N_ALL_SMALL = 3 * N_FEATURES + 4, 3 * N_SMALL + 4

train_raw = add_text_features(spark.read.parquet(f"{PREPARED}/train"))
test_raw  = add_text_features(spark.read.parquet(f"{PREPARED}/test"))
feat_model = features.fit(train_raw)
train = feat_model.transform(train_raw).select("label", "features", "features_small").cache()
test  = feat_model.transform(test_raw).select("label", "features", "features_small").cache()
n_train, n_test = train.count(), test.count()
print(f"[ml] train {n_train:,} (balanced)   test {n_test:,} (real mix)   features {N_ALL:,}")

# ------------------------------------------------------------- 2. MODELS
models = {
    "Factorization Machine (ours)": FMClassifier(factorSize=8, maxIter=int(os.environ.get("FM_ITERS", 100)), stepSize=0.2,
                                                 regParam=1e-4, seed=SEED),
    "Logistic Regression":    LogisticRegression(maxIter=100, regParam=1e-4),
    "Linear SVM":             LinearSVC(maxIter=100, regParam=1e-4),
    "Naive Bayes":            NaiveBayes(modelType="multinomial"),
    "Decision Tree":          DecisionTreeClassifier(maxDepth=10, seed=SEED),
    "Random Forest":          RandomForestClassifier(numTrees=30, maxDepth=10, seed=SEED),
    "Gradient Boosted Trees": GBTClassifier(maxIter=15, maxDepth=5, subsamplingRate=0.5,
                                            featureSubsetStrategy="0.3", seed=SEED),
    "Multilayer Perceptron":  MultilayerPerceptronClassifier(layers=[N_ALL_SMALL, 32, 2], maxIter=100,
                                                             featuresCol="features_small", seed=SEED),
}
if ONLY:
    models = {k: v for k, v in models.items() if any(o.strip() in k for o in ONLY.split(","))}


def evaluate(pred):
    c = {(int(r["label"]), int(r["prediction"])): r["count"]
         for r in pred.groupBy("label", "prediction").count().collect()}
    tn, fp, fn, tp = c.get((0, 0), 0), c.get((0, 1), 0), c.get((1, 0), 0), c.get((1, 1), 0)
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    return {"tn": tn, "fp": fp, "fn": fn, "tp": tp,
            "accuracy": round((tp + tn) / max(tp + tn + fp + fn, 1), 4),
            "precision": round(prec, 4), "recall": round(rec, 4),
            "f1": round(2 * prec * rec / max(prec + rec, 1e-12), 4)}


results, fitted = [], {}
for name, est in models.items():
    t0 = time.time()
    m = est.fit(train)
    secs = round(time.time() - t0, 1)
    r = {"model": name, "train_seconds": secs, **evaluate(m.transform(test))}
    results.append(r)
    fitted[name] = m
    print(f"\n=== {name}  (trained in {secs}s)")
    print("    confusion matrix        pred normal   pred attack")
    print(f"    actual normal   {r['tn']:>14,} {r['fp']:>13,}")
    print(f"    actual attack   {r['fn']:>14,} {r['tp']:>13,}")
    print(f"    accuracy {r['accuracy']:.4f}  precision {r['precision']:.4f}  "
          f"recall {r['recall']:.4f}  F1 {r['f1']:.4f}")

results.sort(key=lambda r: -r["f1"])
print("\n=== COMPARISON (test set, sorted by F1)")
print(f"    {'model':<30}{'accuracy':>9}{'precision':>10}{'recall':>8}{'F1':>8}")
for r in results:
    print(f"    {r['model']:<30}{r['accuracy']:>9.4f}{r['precision']:>10.4f}{r['recall']:>8.4f}{r['f1']:>8.4f}")
spark.createDataFrame(results).write.mode("overwrite").parquet(f"{REPORTS}/ml_comparison")

# ------------------------------------------------------------- 3. SCORE THE NASA LOGS
# Same request normalisation as ml_preprocess.py: URL-decode, lower-case, and keep only the
# query part when there is one.
nasa = None
if "Factorization Machine (ours)" in fitted:
    from urllib.parse import unquote_plus
    url_decode = F.udf(lambda t: unquote_plus(t) if t else t, "string")
    urls = (spark.read.parquet(CURATED).groupBy("endpoint")
            .agg(F.count("*").alias("hits"), F.countDistinct("host").alias("hosts"))
            .withColumn("text", F.lower(F.trim(url_decode("endpoint"))))
            .withColumn("text", F.trim(F.when(F.instr("text", "?") > 0,
                                              F.expr("substring(text, instr(text, '?') + 1)"))
                                        .otherwise(F.col("text"))))
            .filter(F.length("text") > 0))
    scored = (fitted["Factorization Machine (ours)"]
              .transform(feat_model.transform(add_text_features(urls)))
              .withColumn("p_attack", F.round(vector_to_array("probability")[1], 3)))
    flagged = scored.filter("prediction = 1").select("endpoint", "hits", "hosts", "p_attack").cache()
    n_urls, n_flag = scored.count(), flagged.count()
    flag_hits = int(flagged.agg(F.sum("hits")).first()[0] or 0)
    print(f"\n[ml] NASA logs: {n_urls:,} distinct URLs scored, {n_flag:,} flagged as attack "
          f"({flag_hits:,} requests)")
    flagged.orderBy(F.desc("p_attack"), F.desc("hits")).show(20, truncate=80)
    flagged.write.mode("overwrite").parquet(f"{REPORTS}/ml_flagged_urls")
    top = [r.asDict() for r in flagged.orderBy(F.desc("p_attack"), F.desc("hits")).limit(200).collect()]
    # a fixed random sample for checking by hand: precision on real NASA traffic
    sample = [r.asDict() for r in flagged.orderBy(F.rand(SEED)).limit(50).collect()]
    nasa = {"urls_scored": n_urls, "urls_flagged": n_flag, "requests_flagged": flag_hits,
            "top_flagged": top, "review_sample": sample}

# ------------------------------------------------------------- 4. SAVE FOR THE WEBSITE
summary = {"task": "binary classification: 0 = normal, 1 = attack",
           "classes": ["normal", "attack"], "train": n_train, "test": n_test,
           "features": N_ALL, "main_model": "Factorization Machine (ours)",
           "results": results, "nasa": nasa}
os.makedirs(f"{OUTPUT}/ml", exist_ok=True)
with open(f"{OUTPUT}/ml/ml_results.json", "w") as f:
    json.dump(summary, f, indent=1, default=str)


def cm_svg(r):
    """A 2x2 confusion matrix as an inline SVG heat map."""
    cells = [(0, 0, r["tn"], "TN"), (0, 1, r["fp"], "FP"), (1, 0, r["fn"], "FN"), (1, 1, r["tp"], "TP")]
    row_tot = [r["tn"] + r["fp"], r["fn"] + r["tp"]]
    out = ['<svg viewBox="0 0 300 230" width="300" height="230" font-family="sans-serif" font-size="12">',
           '<text x="190" y="14" text-anchor="middle" fill="#555">predicted</text>',
           '<text x="140" y="34" text-anchor="middle">normal</text><text x="240" y="34" text-anchor="middle">attack</text>',
           '<text x="14" y="120" transform="rotate(-90 14 120)" text-anchor="middle" fill="#555">actual</text>',
           '<text x="80" y="90" text-anchor="end">normal</text><text x="80" y="190" text-anchor="end">attack</text>']
    for i, j, v, tag in cells:
        share = v / max(row_tot[i], 1)
        good = i == j
        col = f"rgba(42,120,214,{0.15 + 0.85 * share:.2f})" if good else f"rgba(227,73,72,{0.10 + 0.9 * min(share * 5, 1):.2f})"
        x, y = 90 + j * 100, 40 + i * 100
        txt = "#fff" if (good and share > 0.5) else "#111"
        out.append(f'<rect x="{x}" y="{y}" width="98" height="98" fill="{col}" rx="4"/>')
        out.append(f'<text x="{x+49}" y="{y+48}" text-anchor="middle" font-size="16" font-weight="bold" fill="{txt}">{v:,}</text>')
        out.append(f'<text x="{x+49}" y="{y+68}" text-anchor="middle" fill="{txt}">{tag} · {100*share:.1f}%</text>')
    out.append("</svg>")
    return "".join(out)


rows = "".join(
    f"<tr{' class=ours' if 'ours' in r['model'] else ''}><td>{html.escape(r['model'])}</td>"
    f"<td>{r['accuracy']:.4f}</td><td>{r['precision']:.4f}</td><td>{r['recall']:.4f}</td>"
    f"<td><b>{r['f1']:.4f}</b></td><td>{r['train_seconds']}</td></tr>" for r in results)
bars = "".join(
    f"<div class=bar><span>{html.escape(r['model'])}</span>"
    f"<div><i style='width:{100*r['f1']:.1f}%' class='{'ours' if 'ours' in r['model'] else ''}'></i></div>"
    f"<b>{r['f1']:.4f}</b></div>" for r in results)
cms = "".join(f"<figure><figcaption>{html.escape(r['model'])}</figcaption>{cm_svg(r)}</figure>" for r in results)
nasa_html = ""
if nasa:
    trs = "".join(f"<tr><td>{html.escape(str(u['endpoint']))}</td><td>{u['hits']}</td><td>{u['hosts']}</td>"
                  f"<td>{u['p_attack']}</td></tr>" for u in nasa["top_flagged"][:50])
    nasa_html = (f"<h2>The FM model on the NASA logs</h2><p>{nasa['urls_scored']:,} distinct URLs scored; "
                 f"<b>{nasa['urls_flagged']:,}</b> flagged as attack ({nasa['requests_flagged']:,} requests). "
                 "The NASA logs have no labels, so these are <i>candidates</i>: the review sample in "
                 "ml_results.json is checked by hand to estimate precision.</p>"
                 f"<table><tr><th>URL</th><th>hits</th><th>hosts</th><th>P(attack)</th></tr>{trs}</table>")
page = f"""<!doctype html><html><head><meta charset=utf-8><title>Attack detection: ML results</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:30px auto;padding:0 16px;color:#1f2937}}
h1{{margin-bottom:4px}} table{{border-collapse:collapse;width:100%;margin:12px 0}} td,th{{border-bottom:1px solid #e5e7eb;padding:6px 8px;text-align:left;font-size:14px}}
tr.ours td{{background:#eef3fb;font-weight:600}} .bar{{display:grid;grid-template-columns:230px 1fr 70px;gap:8px;align-items:center;margin:4px 0;font-size:14px}}
.bar div{{background:#f1f2f4;height:16px;border-radius:4px}} .bar i{{display:block;height:16px;background:#9aa4b2;border-radius:4px}} .bar i.ours{{background:#2a78d6}}
.cms{{display:flex;flex-wrap:wrap;gap:14px}} figure{{margin:0;border:1px solid #e5e7eb;border-radius:8px;padding:8px}} figcaption{{font-weight:600;font-size:14px}}
.note{{color:#555;font-size:14px}}</style></head><body>
<h1>Attack detection: Factorization Machine vs 7 Spark MLlib models</h1>
<p class=note>Supervised binary classification, 2 classes (normal / attack). Train {n_train:,} requests (balanced 4:1),
test {n_test:,} requests (real mix, never seen in training). {N_ALL:,} features: character 1-, 2- and 3-grams + 4 counts.</p>
<h2>Comparison (sorted by F1)</h2>
<table><tr><th>Model</th><th>Accuracy</th><th>Precision</th><th>Recall</th><th>F1</th><th>Train time (s)</th></tr>{rows}</table>
<h2>F1 score</h2>{bars}
<h2>Confusion matrices (test set)</h2><p class=note>Rows: actual class. Columns: predicted class. Percent = share of the actual class.</p>
<div class=cms>{cms}</div>{nasa_html}</body></html>"""
with open(f"{OUTPUT}/ml/ml_report.html", "w") as f:
    f.write(page)
print(f"\n[ml] saved {OUTPUT}/ml/ml_results.json and {OUTPUT}/ml/ml_report.html")
spark.stop()
