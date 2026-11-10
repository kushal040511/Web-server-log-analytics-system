#!/usr/bin/env python3
"""
Publish the ML results as Spark tables in HDFS, so the BI dashboards (Apache Superset)
can chart them with Spark SQL like every other report.

The ML jobs write their results as JSON next to the project (output/ml/*.json). This job
turns each one into a Parquet table in the reports zone:

  ml_comparison      8 models: accuracy, precision, recall, F1, false alarms, train time
  ml_confusion       one row per model x actual x predicted cell (TN / FP / FN / TP)
  ml_robustness      top 3 models on 5 random splits (one row per split)
  ml_mcnemar         McNemar's test, FM against each linear model, per split
  ml_crossdataset    train on one dataset, test on the other (one row per seed)
  ml_tuning          the settings grid checked on the validation split
  ml_nasa_audit      the hand-checked categories of the NASA URLs the FM flags
  ml_nasa_flagged    every flagged NASA URL with its category and P(attack)

    docker compose exec client spark-submit /workspace/jobs/publish_ml_tables.py      (make ml-publish)
"""
import sys, os, json
from pyspark.sql import SparkSession, Row

ML_DIR = sys.argv[1] if len(sys.argv) > 1 else "/workspace/output/ml"
REPORTS = sys.argv[2] if len(sys.argv) > 2 else "hdfs://namenode:8020/data/weblogs/reports"

spark = SparkSession.builder.appName("publish-ml-tables").getOrCreate()
spark.sparkContext.setLogLevel("WARN")


def load(name):
    p = os.path.join(ML_DIR, name)
    if not os.path.exists(p):
        print(f"[publish] {name} not found - skipped (run the ML stage first)")
        return None
    with open(p) as f:
        return json.load(f)


def save(name, rows):
    if not rows:
        return
    # one schema for every row: missing keys become null
    keys = list(dict.fromkeys(k for r in rows for k in r))
    df = spark.createDataFrame([Row(**{k: r.get(k) for k in keys}) for r in rows])
    df.write.mode("overwrite").parquet(f"{REPORTS}/{name}")
    print(f"[publish] {name:<18} {df.count():>5} rows -> {REPORTS}/{name}")


def num(v):
    return float(v) if v is not None else None


res = load("ml_results.json")
if res:
    ours = res.get("main_model", "Factorization Machine (ours)")
    comp, conf = [], []
    for rank, r in enumerate(sorted(res["results"], key=lambda r: -r["f1"]), 1):
        comp.append({"rank": rank, "model": r["model"], "is_ours": r["model"] == ours,
                     "accuracy": num(r["accuracy"]), "precision": num(r["precision"]), "recall": num(r["recall"]),
                     "f1": num(r["f1"]), "false_alarms": int(r["fp"]), "missed_attacks": int(r["fn"]),
                     "tn": int(r["tn"]), "tp": int(r["tp"]), "train_seconds": num(r["train_seconds"])})
        for actual, predicted, cell, n in [("normal", "normal", "TN", r["tn"]), ("normal", "attack", "FP", r["fp"]),
                                           ("attack", "normal", "FN", r["fn"]), ("attack", "attack", "TP", r["tp"])]:
            total = (r["tn"] + r["fp"]) if actual == "normal" else (r["fn"] + r["tp"])
            conf.append({"model": r["model"], "actual": actual, "predicted": predicted, "cell": cell,
                         "requests": int(n), "pct_of_actual": round(100.0 * n / max(total, 1), 2)})
    save("ml_comparison", comp)
    save("ml_confusion", conf)
    save("ml_summary", [{"task": res["task"], "classes": len(res["classes"]), "train_requests": res["train"],
                         "test_requests": res["test"], "features": res["features"], "main_model": ours}])

rob = load("ml_robustness.json")
if rob:
    save("ml_robustness", [{"seed": r["seed"], "model": r["model"], "precision": num(r["precision"]),
                            "recall": num(r["recall"]), "f1": num(r["f1"]), "roc_auc": num(r["roc_auc"]),
                            "pr_auc": num(r["pr_auc"]), "train_seconds": num(r["train_seconds"])} for r in rob["runs"]])
    save("ml_mcnemar", [{"seed": t["seed"], "compared_with": t["second"], "only_fm_right": t["only_first_right"],
                         "only_other_right": t["only_second_right"], "chi2": num(t["chi2"]),
                         "p_value": num(t["p_value"]), "significant": t["p_value"] < 0.05} for t in rob["mcnemar"]])

cross = load("ml_crossdataset_runs.json")
if cross:
    save("ml_crossdataset", [{"experiment": r["experiment"].replace("->", "→"), "seed": r["seed"], "model": r["model"],
                              "precision": num(r["precision"]), "recall": num(r["recall"]), "f1": num(r["f1"]),
                              "false_alarms": int(r["fp"]), "missed_attacks": int(r["fn"])} for r in cross])

tune = load("ml_tuning.json")
if tune:
    save("ml_tuning", [{"model": g["model"], "setting": ", ".join(f"{k}={v}" for k, v in g["params"].items()),
                        "validation_f1": num(g["val_f1"]), "train_seconds": num(g["train_seconds"]),
                        "chosen": g["params"] == tune["best"][g["model"]]["params"]} for g in tune["grid"]])

audit = load("nasa_flag_audit.json")
if audit:
    save("ml_nasa_audit", [{"category": c["category"], "urls": c["urls"], "requests": c["requests"], "why": c["why"]}
                           for c in audit["categories"]])
    save("ml_nasa_flagged", [{"endpoint": u["endpoint"], "category": u["category"], "hits": u["hits"],
                              "hosts": u["hosts"], "p_attack": num(u["p_attack"]),
                              "attack_signature": bool(u["attack_signature"])} for u in audit["urls"]])

spark.stop()
