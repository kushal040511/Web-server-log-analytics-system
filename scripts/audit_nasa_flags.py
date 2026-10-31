#!/usr/bin/env python3
"""
Hand-audit of the NASA URLs the Factorization Machine flags as attacks.

The NASA logs have no labels, so every flagged URL was read by hand and put into one
category. The rules below write those decisions down so the audit can be re-run and
checked; anything a rule does not cover lands in "other" for a human to look at.

    python3 scripts/audit_nasa_flags.py output/ml/ml_results.json [curated_parquet_dir]

Writes output/ml/nasa_flag_audit.json next to the input.
"""
import json, os, re, sys
from collections import Counter, defaultdict

SRC = sys.argv[1] if len(sys.argv) > 1 else "output/ml/ml_results.json"
CURATED = sys.argv[2] if len(sys.argv) > 2 else None

# Real attack signatures (SQLi, XSS, traversal, command injection). A flagged URL is only
# counted as a possible attack if it contains one of these.
SIGNATURES = re.compile(r"union\s+select|select\s.+\sfrom|'\s*or\s*'?1'?\s*=|<script|javascript:|onerror\s*=|"
                        r"\.\./\.\.|/etc/passwd|cmd\.exe|;\s*(cat|ls|id|uname|wget)\b|\|\s*(cat|ls|id)\b", re.I)

CATEGORIES = [   # (name, test, why it was flagged)
    ("search query", lambda u: re.search(r"wais\.pl\?|/bin/search\?", u, re.I),
     "free-text site search; words such as 'select', 'transcript' and apostrophes look like SQL to the model"),
    ("CGI with parameters", lambda u: re.search(r"brokerquery|harvest|imagemap", u, re.I),
     "legitimate CGI programs with long key=value strings, '&' and '%3A'"),
    ("static page under /msfc/description/", lambda u: "/msfc/description/" in u.lower(),
     "the path word 'de-SCRIPT-ion' shares character n-grams with '<script'"),
    ("malformed or mistyped request", lambda u: re.search(r"[^\x20-\x7e]|[<>\"\\|`]|;;|http:|http//|file:|c:/|/\.\.\./|\.html?http"
                                                       r"|^/www\.|^/[a-z]+\.ksc\.nasa\.gov/", u, re.I),
     "broken client requests: HTML fragments or another site's address pasted into the URL, binary noise, back-slashes"),
    ("other static page or file", lambda u: True, "ordinary page or image; flagged by unusual characters or length"),
]


def main():
    res = json.load(open(SRC))
    nasa = res["nasa"]
    flagged = nasa["top_flagged"]
    status = {}
    if CURATED:                      # what the server actually answered to these URLs
        import pyarrow.dataset as ds
        t = ds.dataset(CURATED, format="parquet", partitioning="hive", ignore_prefixes=[".", "_"]).to_table(
            columns=["endpoint", "status"], filter=ds.field("endpoint").isin([f["endpoint"] for f in flagged]))
        st = defaultdict(Counter)
        for e, s in zip(t.column("endpoint").to_pylist(), t.column("status").to_pylist()):
            st[e][s] += 1
        status = {e: dict(c) for e, c in st.items()}

    rows, per_cat = [], defaultdict(lambda: {"urls": 0, "requests": 0, "why": ""})
    for f in flagged:
        u = f["endpoint"]
        cat, why = next((n, w) for n, test, w in CATEGORIES if test(u))
        sig = bool(SIGNATURES.search(u))
        rows.append({**f, "category": cat, "attack_signature": sig, "status": status.get(u, {})})
        per_cat[cat]["urls"] += 1; per_cat[cat]["requests"] += f["hits"]; per_cat[cat]["why"] = why

    total_requests = sum(r["hits"] for r in rows)
    out = {"urls_scored": nasa["urls_scored"], "urls_flagged_total": nasa["urls_flagged"], "urls_flagged": len(rows), "requests_flagged": total_requests,
           "with_attack_signature": sum(r["attack_signature"] for r in rows),
           "categories": [{"category": k, **v} for k, v in sorted(per_cat.items(), key=lambda kv: -kv[1]["urls"])],
           "urls": rows}
    if status:
        codes = Counter()
        for r in rows:
            codes.update(r["status"])
        out["status_codes"] = dict(codes)
    path = os.path.join(os.path.dirname(SRC), "nasa_flag_audit.json")
    json.dump(out, open(path, "w"), indent=1)

    print(f"{len(rows)} flagged URLs ({total_requests:,} requests) out of {nasa['urls_scored']:,} scored")
    print(f"{'category':<40}{'URLs':>6}{'requests':>10}")
    for c in out["categories"]:
        print(f"{c['category']:<40}{c['urls']:>6}{c['requests']:>10}")
    print(f"URLs containing a real attack signature: {out['with_attack_signature']}")
    for r in rows:
        if r["attack_signature"]:
            print("   ", r["endpoint"], r["status"])
    if status:
        print("server answers to the flagged requests:", out["status_codes"])
    print("saved", path)


if __name__ == "__main__":
    main()
