"""mitmproxy add-on used once to learn what ChatGPT and Perplexity send.

Logs only the *shape* of each request to the two sites (method, host, path,
content type, size, top-level JSON keys, whether the marker appears, CSP of
HTML pages) - never the content itself.
"""

import json
import os
import re

LOG = os.environ.get("UG_DISCOVER_LOG", "discover.jsonl")
SITES = re.compile(r"(chatgpt\.com|openai\.com|oaistatic\.com|oaiusercontent\.com|perplexity\.ai|pplx|amazonaws\.com|blob\.core\.windows\.net)")
FOLD = re.compile(r"WA[LI][LI]ED.?SENSITIVE", re.I)


def _write(entry):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def request(flow):
    req = flow.request
    origin = req.headers.get("origin", "") or req.headers.get("referer", "")
    if not (SITES.search(req.pretty_host) or SITES.search(origin)):
        return
    body = req.raw_content or b""
    keys = None
    if body[:1] in (b"{", b"["):
        try:
            data = json.loads(body)
            keys = sorted(data.keys()) if isinstance(data, dict) else f"list[{len(data)}]"
        except ValueError:
            pass
    _write({"dir": "req", "method": req.method, "host": req.pretty_host, "path": req.path.split("?")[0][:120],
            "ctype": req.headers.get("content-type", "")[:60], "len": len(body), "origin": origin[:60],
            "marker": bool(FOLD.search(body.decode("latin-1"))), "keys": keys})


def response(flow):
    resp = flow.response
    if not resp or not SITES.search(flow.request.pretty_host):
        return
    ctype = resp.headers.get("content-type", "")
    if "text/html" in ctype or "text/css" in ctype:
        _write({"dir": "resp", "host": flow.request.pretty_host, "path": flow.request.path.split("?")[0][:100],
                "status": resp.status_code, "ctype": ctype[:40], "len": len(resp.raw_content or b""),
                "csp": resp.headers.get("content-security-policy", "")[:400],
                "cache": resp.headers.get("cache-control", "")[:60]})
