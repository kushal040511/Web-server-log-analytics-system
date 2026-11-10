"""LogLens app: saved model, attack checker, headline numbers and pages (Superset calls are not needed)."""
import importlib, os, sys
import pytest
from conftest import run_job, ROOT


@pytest.fixture(scope="session")
def model(ml, pipeline):
    """make ml-model on the test lake."""
    path = pipeline["dir"] / "models" / "fm_attack"
    log = run_job("ml_save_model.py", ml["prepared"], path)
    return dict(path=path, log=log)


@pytest.fixture(scope="session")
def app_client(model, pipeline, published):
    os.environ.update(DATA_BASE=f"file://{pipeline['dir']}", MODEL_PATH=f"file://{model['path']}",
                      WEBHDFS="http://127.0.0.1:9/webhdfs/v1", SUPERSET_URL="http://127.0.0.1:9",
                      APP_SPARK_MASTER="local[1]", PYSPARK_PYTHON=sys.executable)
    sys.path.insert(0, os.path.join(ROOT, "app"))
    import engine, app as webapp
    importlib.reload(engine); importlib.reload(webapp)
    engine.load_model()
    engine.compute_kpis()
    return engine, webapp.app.test_client()


def test_saved_model_reloads_and_scores_the_test_set(model):
    assert "saved to" in model["log"] and "reloaded model on the test set" in model["log"]


def test_normalise_matches_training():
    sys.path.insert(0, os.path.join(ROOT, "app"))
    import engine
    assert engine.normalise("/products.php?id=1%27%20OR%201=1") == "id=1' or 1=1"     # decoded, query part only
    assert engine.normalise("/Shuttle/Countdown/") == "/shuttle/countdown/"           # no query: whole path


def test_checker_scores_requests(app_client):
    engine, c = app_client
    r = c.post("/api/check", json={"request": "/x.php?id=1' union select password from users--"}).get_json()
    assert 0.0 <= r["p_attack"] <= 1.0 and r["attack"] == (r["p_attack"] > 0.5)
    assert "SQL injection words" in r["signals"] and r["length"] == len(r["normalised"])
    assert c.post("/api/check", json={"request": ""}).status_code == 400
    page = c.post("/check", data={"request": "/search?q=<script>alert(1)</script>"})
    assert page.status_code == 200 and b"probability of attack" in page.data


def test_headline_numbers_come_from_hdfs(app_client, pipeline):
    import pandas as pd
    engine, c = app_client
    k = c.get("/api/status").get_json()["kpis"]
    assert k["requests"] == len(pd.read_parquet(pipeline["curated"]))
    assert k["days"] == 21 and k["unusual_hours"] >= 1
    assert k["fm"]["model"] == "Factorization Machine (ours)"


@pytest.mark.parametrize("path", ["/", "/check", "/traffic", "/errors", "/anomalies", "/bots", "/attacks",
                                  "/pipeline", "/about", "/healthz"])
def test_pages(app_client, path):
    _, c = app_client
    assert c.get(path).status_code == 200


def test_unknown_page_and_dashboard(app_client):
    _, c = app_client
    assert c.get("/nope").status_code == 404
    assert c.get("/api/guest-token/nope").status_code == 404
    assert c.get("/api/guest-token/traffic").status_code == 503      # Superset not running in the tests
