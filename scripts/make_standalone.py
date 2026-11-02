#!/usr/bin/env python3
"""
Export the website as plain files that open with no server at all.

Why this exists: the site is normally served by Flask in a container. That is
fine while the project is running, but a submission, an email attachment or a
laptop with no Docker needs something that works on its own. This renders every
report page once, rewrites the links to relative paths, inlines the CSS and JS,
embeds the data, and writes:

    output/site/          index.html, traffic.html, … plus assets/ and tables/
    output/site.zip       the same folder, zipped, for handing in

The /sql page is deliberately left out. It is the one page that runs real
queries against the cluster, so it cannot work from a folder of files — a copy
of it would be a button that always fails. The nav in the export drops it.

    python3 scripts/make_standalone.py            # after `make dashboard`
"""
import json, os, re, shutil, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "output")
SITE = os.path.join(OUT, "site")
DATA = os.path.join(OUT, "dashboard.json")
TABLES = os.path.join(OUT, "tables")

# (route, output filename) — /sql is excluded on purpose, see the docstring.
PAGES = [("/", "index.html"), ("/traffic", "traffic.html"), ("/errors", "errors.html"),
         ("/performance", "performance.html"), ("/bots", "bots.html"),
         ("/about", "about.html")]
ROUTE_TO_FILE = {r: f for r, f in PAGES}

if not os.path.exists(DATA):
    sys.exit("No output/dashboard.json yet — run `make dashboard` first.")

sys.path.insert(0, os.path.join(HERE, "web"))
os.environ.setdefault("OUTPUT_DIR", OUT)
from app import app  # noqa: E402  (needs OUTPUT_DIR set first)

with open(DATA) as fh:
    payload = fh.read()

# The pages fetch /api/data. Over file:// that request cannot succeed, so hand
# them the data directly instead — same shape, so no page code changes.
SHIM = """<script>
/* Static export: serve the embedded payload instead of the /api/data request.
   Everything downstream is unchanged, so these pages behave exactly as served. */
window.__DASHBOARD__ = %s;
window.__DL_BASE__ = "tables/";   /* the table download buttons are built in JS */
(function (real) {
  window.fetch = function (url, opts) {
    if (String(url).indexOf("/api/data") !== -1)
      return Promise.resolve({ ok: true, status: 200,
                               json: function () { return Promise.resolve(window.__DASHBOARD__); } });
    return real ? real.apply(window, arguments) : Promise.reject(new Error("offline"));
  };
})(window.fetch);
</script>"""


def rewrite(html):
    """Turn server routes into relative files, and inline the assets."""
    # assets: /static/css/app.css -> assets/app.css
    html = re.sub(r'(?:href|src)="/static/(?:css|js)/([^"?]+)(?:\?[^"]*)?"',
                  lambda m: ('href="assets/%s"' if m.group(1).endswith(".css")
                             else 'src="assets/%s"') % m.group(1), html)
    # downloads: /download/x.csv -> tables/x.csv
    html = html.replace('href="/download/', 'href="tables/')
    # nav and in-page links: /traffic -> traffic.html
    for route, fname in sorted(ROUTE_TO_FILE.items(), key=lambda kv: -len(kv[0])):
        html = html.replace('href="%s"' % route, 'href="%s"' % fname)
    # the SQL page is not exported; drop its nav entry rather than link to nothing
    html = re.sub(r'\s*<a href="/(?:sql|logs|ml)"[^>]*>.*?</a>', "", html, flags=re.S)
    html = html.replace('href="/logs"', 'href="about.html"')
    html = html.replace('href="/sql"', 'href="about.html"')
    html = html.replace('href="/ml"', 'href="about.html"')
    # inject the data shim just before the first script tag that needs it
    return html.replace("</head>", SHIM % payload + "\n</head>", 1)


shutil.rmtree(SITE, ignore_errors=True)
os.makedirs(os.path.join(SITE, "assets"), exist_ok=True)

written = []
with app.test_client() as client:
    for route, fname in PAGES:
        resp = client.get(route)
        if resp.status_code != 200:
            sys.exit(f"{route} returned {resp.status_code} — is the data exported?")
        html = rewrite(resp.get_data(as_text=True))
        with open(os.path.join(SITE, fname), "w") as fh:
            fh.write(html)
        written.append(fname)

# every stylesheet and script the pages link, not a hand-kept list that drifts
for sub in ("css", "js"):
    folder = os.path.join(HERE, "web", "static", sub)
    for name in sorted(os.listdir(folder)):
        if name.endswith((".css", ".js")):
            shutil.copy(os.path.join(folder, name), os.path.join(SITE, "assets", name))

if os.path.isdir(TABLES):
    shutil.copytree(TABLES, os.path.join(SITE, "tables"))

archive = shutil.make_archive(os.path.join(OUT, "site"), "zip", SITE)

size = sum(os.path.getsize(os.path.join(dp, f))
           for dp, _, fs in os.walk(SITE) for f in fs)
print(f"[standalone] {len(written)} pages -> {SITE}")
print(f"[standalone]   {', '.join(written)}")
print(f"[standalone] {size/1024/1024:.1f} MB unpacked · "
      f"{os.path.getsize(archive)/1024/1024:.1f} MB zipped")
print(f"[standalone] open {os.path.join(SITE, 'index.html')}")
print("[standalone] the SQL page is not included — it needs the live cluster")
