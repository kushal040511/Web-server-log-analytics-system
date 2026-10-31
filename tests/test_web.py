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
    for f in ("ml_results.json", "ml_crossdataset.json"):
        shutil.copy(os.path.join(ROOT, "docs", "ml_results", f), out / "ml" / f)
    d = c.get("/api/ml").get_json()
    assert d["results"][0]["f1"] >= d["results"][-1]["f1"]
    assert len(c.get("/api/ml/cross").get_json()) == 6


def test_unknown_table_is_404(client):
    c, _ = client
    assert c.get("/api/table/does_not_exist").status_code == 404
    assert c.get("/download/../../etc/passwd.csv").status_code in (400, 404)
