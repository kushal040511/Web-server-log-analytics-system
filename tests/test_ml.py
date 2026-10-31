"""ML preprocessing, training, NASA scoring and the cross-dataset test on a small labelled sample."""
import pandas as pd

def test_preprocessing_split_is_clean(ml):
    train = pd.read_parquet(ml["prepared"] / "train")
    test = pd.read_parquet(ml["prepared"] / "test")
    assert set(train["label"]) == {0, 1} and set(test["label"]) == {0, 1}
    assert not set(train["text"]) & set(test["text"])            # no request is in both sets
    for df in (train, test):                                     # cleaned: lower-case, spaces trimmed (Spark trim), not empty
        assert (df["text"] == df["text"].str.lower().str.strip(" ")).all() and (df["text"].str.len() > 0).all()
    n_att = (train["label"] == 1).sum()
    assert (train["label"] == 0).sum() <= 4 * n_att + 5          # balanced 4 normal : 1 attack
    assert "duplicates removed" in ml["logs"]["prep"] and "conflicting labels removed" in ml["logs"]["prep"]


def test_models_trained_with_valid_confusion_matrices(ml):
    res = ml["results"]
    assert res["classes"] == ["normal", "attack"]
    names = {r["model"] for r in res["results"]}
    assert names == {"Factorization Machine (ours)", "Logistic Regression", "Linear SVM"}
    for r in res["results"]:
        assert r["tn"] + r["fp"] + r["fn"] + r["tp"] == res["test"]
        p, rec = r["tp"] / max(r["tp"] + r["fp"], 1), r["tp"] / max(r["tp"] + r["fn"], 1)
        assert abs(r["precision"] - round(p, 4)) < 1e-4 and abs(r["recall"] - round(rec, 4)) < 1e-4
        assert r["f1"] > 0.5, r                                  # far better than chance even on 1,250 rows


def test_nasa_scoring(ml):
    nasa = ml["results"]["nasa"]
    assert nasa["urls_scored"] > 0 and 0 <= nasa["urls_flagged"] <= nasa["urls_scored"]
    assert (ml["out"] / "ml" / "ml_report.html").exists()


def test_cross_dataset(ml):
    import json
    cross = json.load(open(ml["out"] / "ml" / "ml_crossdataset.json"))
    assert len(cross) == 6 and {c["experiment"][0] for c in cross} == {"A", "B"}
    assert all(c["runs"] == 2 and c["f1_std"] >= 0 for c in cross)
    runs = json.load(open(ml["out"] / "ml" / "ml_crossdataset_runs.json"))
    assert len(runs) == 12


def test_robustness(ml):
    import json
    rob = json.load(open(ml["out"] / "ml" / "ml_robustness.json"))
    assert rob["seeds"] == [1, 2] and len(rob["runs"]) == 6 and len(rob["mcnemar"]) == 4
    for s in rob["summary"]:
        assert 0.5 <= s["roc_auc_mean"] <= 1 and 0 <= s["pr_auc_mean"] <= 1 and s["f1_std"] >= 0
    for t in rob["mcnemar"]:
        assert 0 <= t["p_value"] <= 1


def test_tuning_uses_validation_only(ml):
    import json
    t = json.load(open(ml["out"] / "ml" / "ml_tuning.json"))
    assert len(t["grid"]) == 12 and set(t["best"]) == {"Factorization Machine (ours)", "Linear SVM", "Logistic Regression"}
    assert "test set not used" in ml["logs"]["tune"]


def test_nasa_audit_script(ml):
    import subprocess, sys, json, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, os.path.join(root, "scripts", "audit_nasa_flags.py"),
                    str(ml["out"] / "ml" / "ml_results.json")], check=True, capture_output=True)
    a = json.load(open(ml["out"] / "ml" / "nasa_flag_audit.json"))
    assert sum(c["urls"] for c in a["categories"]) == a["urls_flagged"]
