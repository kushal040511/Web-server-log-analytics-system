"""Every page and API of the website, with and without ML results."""
import importlib, json, os, shutil, sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_client(out):
    os.environ["OUTPUT_DIR"] = str(out)
    os.environ.pop("DASHBOARD_JSON", None)
    sys.path.insert(0, os.path.join(ROOT, "web"))
    import app as webapp
    webapp = importlib.reload(webapp)
    return webapp.app.test_client()


@pytest.fixture
def client(pipeline, tmp_path):
    out = tmp_path / "output"
    shutil.copytree(pipeline["out"], out, ignore=shutil.ignore_patterns("ml"))
    return make_client(out), out


@pytest.mark.parametrize("path", ["/", "/traffic", "/errors", "/performance", "/ml", "/healthz",
                                  "/bots", "/logs", "/sql", "/about",
                                  "/api/data", "/api/tables", "/api/table/top_urls",
                                  "/download/top_urls.csv", "/download/top_urls.json"])
def test_pages_and_api(client, path):
    c, _ = client
    r = c.get(path)
    assert r.status_code == 200, (path, r.status_code)


def test_ml_api_empty_then_filled(client):
    c, out = client
    assert c.get("/api/ml").status_code == 404                     # page shows "run make ml"
    os.makedirs(out / "ml")
    assert c.get("/api/ml/tuning").status_code == 404
    for f in ("ml_results.json", "ml_crossdataset.json", "ml_tuning.json"):
        shutil.copy(os.path.join(ROOT, "docs", "ml_results", f), out / "ml" / f)
    d = c.get("/api/ml").get_json()
    assert d["results"][0]["f1"] >= d["results"][-1]["f1"]
    assert len(c.get("/api/ml/cross").get_json()) == 6
    assert c.get("/api/ml/tuning").get_json()["best"]["Linear SVM"]["params"]


def test_unknown_table_is_404(client):
    c, _ = client
    assert c.get("/api/table/does_not_exist").status_code == 404
    assert c.get("/download/../../etc/passwd.csv").status_code in (400, 404)


def test_sql_console_is_read_only(client):
    c, _ = client
    for q in ("DROP TABLE access_logs", "INSERT INTO t VALUES (1)", "rm -rf /"):
        r = c.post("/api/query", json={"sql": q})
        assert r.status_code == 400, q                            # rejected before Spark is touched
    assert c.post("/api/query", json={"sql": ""}).status_code == 400


def test_log_explorer_searches_the_raw_files(pipeline, tmp_path):
    out = tmp_path / "output"
    shutil.copytree(pipeline["out"], out, ignore=shutil.ignore_patterns("ml"))
    c = make_client(out)
    assert c.get("/api/logs/meta").status_code == 404             # no raw files yet: "run make data"
    (tmp_path / "data").mkdir()
    # the explorer binary-searches by time, so it needs a time-ordered file, as real NASA logs are
    from datetime import datetime
    lines = [l for l in open(pipeline["raw"] / "access_log_test") if "[" in l]
    lines.sort(key=lambda l: datetime.strptime(l.split("[")[1][:20], "%d/%b/%Y:%H:%M:%S"))
    (tmp_path / "data" / "access_log_test").write_text("".join(lines))
    c = make_client(out)
    meta = c.get("/api/logs/meta").get_json()
    assert meta["files"][0]["from"].startswith("1995-07-01")
    r = c.get("/api/logs/search?status=404&limit=5").get_json()
    assert len(r["rows"]) == 5 and all(row[4] == 404 for row in r["rows"])
    w = c.get("/api/logs/window?start=1995-07-03T14:00&seconds=3600").get_json()
    assert len(w["rows"]) >= 600                                  # the injected burst hour
