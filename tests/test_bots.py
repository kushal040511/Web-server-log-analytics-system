"""GMM bot detection (jobs/bot_detection.py) on a small log with known crawlers and human visitors."""
import json, random
from datetime import datetime, timedelta
import pytest
from conftest import run_job


def bot_log(path, seed=7):
    """40 human hosts (pages + images, short gaps) and 6 crawlers (HEAD, no images, slow and even pacing)."""
    rnd = random.Random(seed)
    start, lines = datetime(1995, 7, 1), []
    pages = [f"/shuttle/missions/sts-{n}/mission.html" for n in range(60, 80)]
    imgs = ["/images/NASA-logosmall.gif", "/images/KSC-logosmall.gif", "/images/launch.jpg"]

    def add(host, t, method, url, status=200, size=2000):
        lines.append(f'{host} - - [{t:%d/%b/%Y:%H:%M:%S} -0400] "{method} {url} HTTP/1.0" {status} {size}')

    for h in range(40):
        host = f"user{h}.example.com"
        for s in range(rnd.randint(2, 4)):                       # a few browsing sessions
            t = start + timedelta(days=rnd.randrange(20), hours=rnd.randrange(8, 22))
            for _ in range(rnd.randint(8, 14)):
                t += timedelta(seconds=rnd.randint(2, 40))
                add(host, t, "GET", rnd.choice(pages))
                for img in rnd.sample(imgs, 2):
                    add(host, t + timedelta(seconds=1), "GET", img, size=900)
    for b in range(6):
        host = f"crawler{b}.search.example.org"
        t = start + timedelta(hours=b)
        add(host, t, "GET", "/robots.txt", size=120)
        for i in range(120):                                     # many distinct URLs, one by one
            t += timedelta(seconds=rnd.randint(500, 700))
            add(host, t, "HEAD" if i % 2 else "GET", f"/dir{i % 15}/page{i}.html")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


@pytest.fixture(scope="module")
def bots(tmp_path_factory):
    d = tmp_path_factory.mktemp("bots")
    raw, curated, reports, out = d / "raw", d / "curated", d / "reports", d / "output"
    raw.mkdir()
    bot_log(raw / "access_log_bots")
    run_job("etl.py", raw, curated)
    log = run_job("bot_detection.py", curated, reports)
    run_job("export_dashboard.py", reports, curated, out)
    summary = json.loads((reports / "bot_detection_summary").glob("part-*").__next__().read_text())
    return dict(summary=summary, log=log, out=out)


def test_gmm_clusters_every_host(bots):
    s = bots["summary"]
    assert s["algorithm"].startswith("Gaussian Mixture Model") and s["k"] == 3
    assert s["hosts_clustered"] == 46                            # every host has >= 20 requests
    assert sum(r["hosts"] for r in s["cluster_table"]) == 46
    assert -1.0 <= s["silhouette"] <= 1.0


def test_automated_component_holds_the_crawlers(bots):
    s = bots["summary"]
    assert s["validation"]["known_crawlers"] == 6
    assert s["validation"]["caught"] == 6                        # all robots.txt requesters
    assert all(h.startswith("crawler") for h in s["flagged_examples"])


def test_export_carries_the_bot_summary(bots):
    d = json.load(open(bots["out"] / "dashboard.json"))
    assert d["bots"]["hosts_flagged"] == bots["summary"]["hosts_flagged"]
