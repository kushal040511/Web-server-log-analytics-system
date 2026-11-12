"""
The LogLens app's link to the cluster.

* Spark  - one long-lived SparkSession (local mode inside the app container) that reads the
           Parquet tables in HDFS for the headline numbers and runs the saved attack-detection
           model (hdfs:///models/fm_attack) on requests typed into the app.
* HDFS   - live sizes of the data zones from the NameNode's WebHDFS REST API.
* Superset - short-lived guest tokens so the browser can show a dashboard (embedded mode).

Charts are never drawn by the app: every chart on every page is an Apache Superset
dashboard, embedded with a guest token that can only view that one dashboard.
"""
import os, re, json, time, threading
from urllib.parse import unquote_plus
import requests

HDFS = os.environ.get("HDFS", "hdfs://namenode:8020")
WEBHDFS = os.environ.get("WEBHDFS", "http://namenode:9870/webhdfs/v1")
BASE = os.environ.get("DATA_BASE", f"{HDFS}/data/weblogs")
MODEL = os.environ.get("MODEL_PATH", f"{HDFS}/models/fm_attack")
SUPERSET = os.environ.get("SUPERSET_URL", "http://superset:8088")              # from the app's server
SUPERSET_PUBLIC = os.environ.get("SUPERSET_PUBLIC_URL", "http://localhost:8088")  # from the browser
SUPERSET_USER = os.environ.get("SUPERSET_USER", "admin")
SUPERSET_PASSWORD = os.environ.get("SUPERSET_PASSWORD", "admin")

# ------------------------------------------------------------------ Spark
_spark, _model, _lock = None, None, threading.Lock()
STATUS = {"spark": "starting", "model": "not loaded", "kpis": "not computed"}
KPIS = {}


def spark():
    global _spark
    if _spark is None:
        from pyspark.sql import SparkSession
        _spark = (SparkSession.builder.appName("loglens-app").master(os.environ.get("APP_SPARK_MASTER", "local[1]"))
                  .config("spark.driver.memory", os.environ.get("APP_SPARK_MEMORY", "1g"))
                  .config("spark.sql.session.timeZone", "America/New_York")
                  .config("spark.sql.shuffle.partitions", "4")
                  .config("spark.ui.enabled", "false")
                  .config("spark.eventLog.enabled", "false")
                  .getOrCreate())
        _spark.sparkContext.setLogLevel("ERROR")
        STATUS["spark"] = "ready"
    return _spark


def compute_kpis():
    """Headline numbers, each a Spark query on HDFS (run once at start-up, then cached)."""
    from pyspark.sql import functions as F
    s = spark()
    out = {}

    def read(name):
        return s.read.parquet(f"{BASE}/reports/{name}")

    def safe(key, fn):
        try:
            out[key] = fn()
        except Exception as e:  # a stage has not run yet
            out[key] = None
            print(f"[app] kpi {key}: {type(e).__name__}: {str(e)[:120]}")

    safe("requests", lambda: int(read("daily").agg(F.sum("hits")).first()[0]))
    safe("days", lambda: read("daily").count())
    safe("errors_pct", lambda: (lambda r: 100.0 * r[0] / r[1])(
        read("status_distribution").agg(F.sum(F.when(F.col("status") >= 400, F.col("hits"))), F.sum("hits")).first()))
    safe("unusual_hours", lambda: read("anomaly_hours").count())
    safe("top_z", lambda: float(read("anomaly_hours").agg(F.max("z")).first()[0]))
    safe("automated_hosts", lambda: int(read("bot_summary").first()["hosts_flagged"]))
    safe("automated_pct", lambda: float(read("bot_summary").first()["pct_requests_flagged"]))
    safe("fm", lambda: read("ml_comparison").filter(F.col("is_ours")).first().asDict())
    safe("models", lambda: read("ml_comparison").count())
    safe("hosts_gb", lambda: (lambda r: {"hosts": int(r[0]), "gb": float(r[1]) / 1024 ** 3})(
        s.read.parquet(f"{BASE}/curated").agg(F.countDistinct("host"), F.sum("bytes")).first()))
    KPIS.clear()
    KPIS.update(out)
    STATUS["kpis"] = time.strftime("%H:%M:%S")


def load_model():
    global _model
    from pyspark.ml import PipelineModel
    _model = PipelineModel.load(MODEL)
    STATUS["model"] = "ready"


def warm_up():
    """Start Spark, load the model and compute the headline numbers in the background."""
    def run():
        for name, fn in (("model", load_model), ("kpis", compute_kpis)):
            try:
                fn()
            except Exception as e:
                STATUS[name] = f"unavailable: {type(e).__name__}"
                print(f"[app] {name}: {e}")
    threading.Thread(target=run, daemon=True).start()


# ------------------------------------------------------------------ attack detection
SPECIAL = r"""['"<>;()=%&|`$\-\.\\/*+#!{}\[\]]"""
KEYWORD_GROUPS = [
    ("SQL injection words", r"select|union|insert|drop|update|delete|sleep\(|benchmark"),
    ("Script / XSS markers", r"<script|javascript:|onerror|onload|alert\("),
    ("Path traversal", r"\.\./|/etc/passwd|win\.ini|boot\.ini"),
    ("Shell command", r";\s*(ls|cat|id|uname|wget|curl)|\|\s*(ls|cat|id)|`"),
]


def normalise(raw):
    """Same request normalisation as the training data: URL-decode, lower-case, trim,
    and keep only the query part when there is one (the bias fix)."""
    t = unquote_plus(raw or "").lower().strip()
    if "?" in t:
        t = t[t.index("?") + 1:].strip()
    return t


def add_text_features(df):   # identical to jobs/ml_train.py and jobs/ml_save_model.py
    from pyspark.sql import functions as F
    return (df.withColumn("chars", F.expr("filter(split(text, ''), c -> c != '')"))
        .withColumn("len", F.length("text").cast("double"))
        .withColumn("n_special", (F.length("text") - F.length(F.regexp_replace("text", SPECIAL, ""))).cast("double"))
        .withColumn("n_digits", (F.length("text") - F.length(F.regexp_replace("text", "[0-9]", ""))).cast("double"))
        .withColumn("n_keywords", F.lit(0.0)
            + F.col("text").rlike(KEYWORD_GROUPS[0][1]).cast("double")
            + F.col("text").rlike(KEYWORD_GROUPS[1][1]).cast("double")
            + F.col("text").rlike(KEYWORD_GROUPS[2][1]).cast("double")
            + F.col("text").rlike(KEYWORD_GROUPS[3][1]).cast("double")))


def score(raw):
    """Run the saved Factorization Machine (Spark ML PipelineModel) on one request."""
    from pyspark.ml.functions import vector_to_array
    from pyspark.sql import functions as F
    text = normalise(raw)
    if not text:
        raise ValueError("empty request")
    if _model is None:
        raise RuntimeError("the attack-detection model is not loaded yet (run: make ml-model)")
    t0 = time.time()
    with _lock:
        df = spark().createDataFrame([(text,)], ["text"])
        row = (_model.transform(add_text_features(df))
               .select("prediction", vector_to_array("probability")[1].alias("p"),
                       "len", "n_special", "n_digits").first())
    return {
        "input": raw, "normalised": text, "attack": int(row["prediction"]) == 1, "p_attack": float(row["p"]),
        "length": int(row["len"]), "special": int(row["n_special"]), "digits": int(row["n_digits"]),
        "signals": [name for name, rx in KEYWORD_GROUPS if re.search(rx, text)],
        "ms": int(1000 * (time.time() - t0)),
    }


# ------------------------------------------------------------------ HDFS
def hdfs_summary():
    """Size, file count and replicated size of each data zone, live from the NameNode."""
    zones = [("Raw logs", "/data/weblogs/raw"), ("Curated (Parquet)", "/data/weblogs/curated"),
             ("Reports", "/data/weblogs/reports"), ("Labelled attacks", "/data/attacks"), ("Saved model", "/models")]
    out = []
    for label, path in zones:
        try:
            r = requests.get(f"{WEBHDFS}{path}", params={"op": "GETCONTENTSUMMARY", "user.name": "root"}, timeout=5)
            c = r.json()["ContentSummary"]
            out.append({"zone": label, "path": path, "mb": c["length"] / 2 ** 20, "files": c["fileCount"],
                        "replicated_mb": c["spaceConsumed"] / 2 ** 20})
        except Exception:
            out.append({"zone": label, "path": path, "mb": None, "files": None, "replicated_mb": None})
    return out


# ------------------------------------------------------------------ Superset (embedded mode)
_superset = {"token": None, "at": 0, "uuids": {}}


def _superset_session():
    if _superset["token"] and time.time() - _superset["at"] < 600:
        return _superset["token"]
    r = requests.post(f"{SUPERSET}/api/v1/security/login", json={
        "username": SUPERSET_USER, "password": SUPERSET_PASSWORD, "provider": "db", "refresh": False}, timeout=10)
    r.raise_for_status()
    _superset.update(token=r.json()["access_token"], at=time.time())
    return _superset["token"]


def embedded_uuid(slug):
    """The embedded-mode id of a dashboard (set by superset/build_dashboards.py)."""
    if slug not in _superset["uuids"]:
        h = {"Authorization": "Bearer " + _superset_session()}
        d = requests.get(f"{SUPERSET}/api/v1/dashboard/{slug}/embedded", headers=h, timeout=10)
        d.raise_for_status()
        _superset["uuids"][slug] = d.json()["result"]["uuid"]
    return _superset["uuids"][slug]


def guest_token(slug):
    """A guest token that can view only this one dashboard, for the browser."""
    uuid = embedded_uuid(slug)
    h = {"Authorization": "Bearer " + _superset_session()}
    r = requests.post(f"{SUPERSET}/api/v1/security/guest_token/", headers=h, timeout=10, json={
        "user": {"username": "loglens", "first_name": "LogLens", "last_name": "viewer"},
        "resources": [{"type": "dashboard", "id": uuid}], "rls": []})
    r.raise_for_status()
    return {"token": r.json()["token"], "uuid": uuid, "supersetDomain": SUPERSET_PUBLIC}
