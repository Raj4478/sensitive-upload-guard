"""Which hosts belong to the protected sites, and which of their requests are tracking.

Only these hosts are decrypted by the proxy. Everything else is passed through
untouched (still encrypted) or never reaches the proxy at all (see pac.py).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# Main web-app hosts: decrypted, inspected, and their pages recoloured.
SITE_HOSTS = {
    "ChatGPT": re.compile(r"(^|\.)(chatgpt\.com|chat\.openai\.com)$"),
    "Perplexity": re.compile(r"(^|\.)perplexity\.ai$"),
}

# Storage services the sites upload files to. Requests to these hosts are
# routed to the proxy (pac.py) but only decrypted when the host was named by a
# protected site (learned from its responses) or looks like the site's own bucket.
STORAGE_SUFFIXES = (".oaiusercontent.com", ".blob.core.windows.net", ".amazonaws.com", ".cloudfront.net")
STORAGE_HINTS = re.compile(r"(oaiusercontent|openai|chatgpt|ppl-ai|pplx|perplexity)", re.I)

# Requests that report what the user did rather than send what they wrote.
# They never open a dialog: they go through only if the same text was approved.
TRACKING_PATH = re.compile(r"(analytics|/event|telemetry|metrics|/ces/|rgstr|/log\b|/logs\b|track|beacon|sentry|"
                           r"/collect|/v1/t\b|/rum)", re.I)
TRACKING_HOST = re.compile(r"(datadog|segment|statsig|sentry|singular|count\.|intercom|amplitude|mixpanel)", re.I)


def extra_test_hosts() -> dict[str, re.Pattern]:
    """Tests can protect a local page: UPLOAD_GUARD_TEST_HOSTS=localhost."""
    raw = os.environ.get("UPLOAD_GUARD_TEST_HOSTS", "")
    return {"Test site": re.compile("^(" + "|".join(re.escape(h) for h in raw.split(",") if h) + ")$")} if raw else {}


def site_of_host(host: str) -> str | None:
    host = (host or "").lower().rstrip(".")
    for name, pattern in {**SITE_HOSTS, **extra_test_hosts()}.items():
        if pattern.search(host):
            return name
    return None


def site_of_origin(origin: str) -> str | None:
    """The site a request came from, using its Origin or Referer header."""
    match = re.match(r"https?://([^/:]+)", origin or "")
    return site_of_host(match.group(1)) if match else None


def is_storage_host(host: str) -> bool:
    host = (host or "").lower()
    return host.endswith(STORAGE_SUFFIXES)


def is_tracking(host: str, path: str) -> bool:
    return bool(TRACKING_HOST.search(host or "") or TRACKING_PATH.search(path or ""))


@dataclass(frozen=True)
class Target:
    """Why a connection is decrypted."""
    site: str | None
    reason: str     # "site", "storage", or "" (pass through)
