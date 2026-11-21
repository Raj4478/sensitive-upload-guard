"""mitmproxy add-on: inspect, hold, and allow or block what the browser sends.

Hooks (mitmproxy calls them for every connection / request / response):

* ``tls_clienthello`` - decide per connection whether to decrypt it at all.
  Only the protected sites and their upload storage are decrypted; everything
  else passes through still encrypted.
* ``request`` - for POST/PUT/PATCH to a protected site (or a cross-site upload
  whose Origin is a protected site): parse the body; files are inspected
  (text/PDF/image + OCR), prompt text is matched. If the marker is found the
  request is **held** while the native dialog is open, then forwarded unchanged
  (Continue) or answered locally with an error (Cancel) so it never leaves the PC.
  Tracking beacons that carry the marker never open a dialog: they go through
  only if the user already approved that text.
* ``response`` - learn upload hosts and file names from the sites' replies, and
  add the response-colour stylesheet to their HTML pages.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from urllib.parse import urlsplit

from mitmproxy import http

from upload_guard import detector, inspector, messages, payload, sites
from upload_guard.config import Settings
from upload_guard.decisions import DecisionCenter, Event

log = logging.getLogger(__name__)

SUPPORTED_KINDS = {"text", "pdf", "image"}
NAME_MEMORY_SECONDS = 300
STYLE_ID = "upload-guard-response-colour"

RESPONSE_CSS = """
[data-message-role="assistant"] [data-assistant-markdown-root],
[data-message-role="assistant"] [data-assistant-markdown-root] *,
[data-message-author-role="assistant"] .markdown,
[data-message-author-role="assistant"] .markdown *,
[data-renderer="lm"]:not([data-message-bubble] *),
[data-renderer="lm"]:not([data-message-bubble] *) *,
[id^="markdown-content-"],
[id^="markdown-content-"] * { color: %s !important; }
"""


class GuardAddon:
    def __init__(self, settings: Settings, center: DecisionCenter):
        self.settings = settings
        self.center = center
        self.learned_hosts: set[str] = set()
        self._names_by_size: dict[int, tuple[str, float]] = {}
        self._names_by_path: dict[str, tuple[str, float]] = {}

    # ================================================================ which connections to decrypt

    def should_decrypt(self, host: str) -> bool:
        host = (host or "").lower()
        if sites.site_of_host(host):
            return True
        if host in self.learned_hosts:
            return True
        return sites.is_storage_host(host) and bool(sites.STORAGE_HINTS.search(host))

    def tls_clienthello(self, data) -> None:
        sni = data.client_hello.sni or ""
        if not self.should_decrypt(sni):
            data.ignore_connection = True      # pass through, still encrypted

    # ================================================================ requests

    def requestheaders(self, flow: http.HTTPFlow) -> None:
        # Tests only: a made-up host (e.g. upload-guard.test) is served by a local
        # test server. Browsers never proxy "localhost", so tests need a real-looking name.
        upstream = os.environ.get("UPLOAD_GUARD_TEST_UPSTREAM")
        if upstream and sites.site_of_host(flow.request.pretty_host) == "Test site":
            flow.request.host = upstream

    async def request(self, flow: http.HTTPFlow) -> None:
        req = flow.request
        if req.method not in ("POST", "PUT", "PATCH"):
            return
        host = req.pretty_host
        site = sites.site_of_host(host) or sites.site_of_origin(req.headers.get("origin") or req.headers.get("referer", ""))
        if not site:
            return
        body = req.get_content(strict=False) or b""
        if not body:
            return
        self._remember_announced_names(body)
        parsed = payload.parse(body, req.headers.get("content-type", ""), req.path)
        tracking = sites.is_tracking(host, req.path)

        if parsed.files and not tracking:
            if not await self._check_files(flow, site, parsed.files):
                return
        if parsed.has_marker_text:
            if tracking:
                await self._check_tracking(flow, site, parsed.text)
            else:
                await self._check_prompt(flow, site, parsed)

    async def _check_prompt(self, flow: http.HTTPFlow, site: str, parsed: payload.Payload) -> None:
        prompt = parsed.prompt or parsed.text
        finding = inspector.inspect_prompt(prompt)
        key = "prompt:" + detector.fold(prompt)[:4000]
        decision = await self.center.ask(key, site, messages.dialog_content(site, finding, prompt))
        preview = " ".join(prompt.split())[:80]
        self.center.record(Event(time.time(), site, "prompt", preview, "marker in prompt", decision,
                                 hashlib.sha256(prompt.encode()).hexdigest()))
        if decision == "continue":
            self.center.approve(site, prompt)
        else:
            self._block(flow, "Blocked by Upload Guard: this prompt contains WALLED_SENSITIVE. "
                              "Your text is still in the box - edit it and send again.")

    async def _check_files(self, flow: http.HTTPFlow, site: str, files: list[payload.UploadedFile]) -> bool:
        """Inspect every file in the request. Returns False if the request was blocked."""
        for item in files:
            name = self._resolve_name(item, flow.request.path)
            finding = await asyncio.to_thread(inspector.inspect_file, name, item.data, item.mime)
            digest = hashlib.sha256(item.data).hexdigest()
            needs_decision = finding.detected or (not finding.inspected and finding.kind in SUPPORTED_KINDS)
            if not needs_decision:
                self.center.record(Event(time.time(), site, "file", name, f"clean {finding.kind}", "allow", digest))
                continue
            if not finding.detected and self.settings.unreadable_policy != "ask":
                decision = "continue" if self.settings.unreadable_policy == "allow" else "cancel"
            else:
                decision = await self.center.ask("file:" + digest, site, messages.dialog_content(site, finding))
            detail = finding.location if finding.detected else "could not be checked: " + "; ".join(finding.errors)[:120]
            self.center.record(Event(time.time(), site, "file", name, detail, decision, digest))
            if decision != "continue":
                self._block(flow, f"Blocked by Upload Guard: {name} contains WALLED_SENSITIVE.")
                return False
        return True

    async def _check_tracking(self, flow: http.HTTPFlow, site: str, text: str) -> None:
        """Analytics beacons repeating the prompt: never a dialog; allowed only if approved."""
        await self.center.wait_until_idle(site)
        if self.center.was_approved(site, text):
            return
        self.center.record(Event(time.time(), site, "tracking", flow.request.pretty_host + flow.request.path.split("?")[0][:60],
                                 "tracking request carried unapproved marker text", "blocked"))
        flow.response = http.Response.make(204, b"", {"cache-control": "no-store"})

    @staticmethod
    def _block(flow: http.HTTPFlow, message: str) -> None:
        flow.response = http.Response.make(
            403, json.dumps({"detail": message, "error": message, "message": message}),
            {"content-type": "application/json", "cache-control": "no-store", "x-upload-guard": "blocked"})

    # ================================================================ file names

    def _remember_announced_names(self, body: bytes) -> None:
        now = time.monotonic()
        for name, size in payload.file_names_announced(body):
            if size:
                self._names_by_size[size] = (name, now)
            self._last_announced = (name, now)

    def _resolve_name(self, item: payload.UploadedFile, path: str) -> str:
        """Raw uploads to blob storage carry no file name; use what the site announced."""
        now = time.monotonic()
        if item.name and re.search(r"\.\w{1,5}$", item.name):
            return item.name
        by_path = self._names_by_path.get(path.split("?")[0])
        if by_path and now - by_path[1] < NAME_MEMORY_SECONDS:
            return by_path[0]
        by_size = self._names_by_size.get(len(item.data))
        if by_size and now - by_size[1] < NAME_MEMORY_SECONDS:
            return by_size[0]
        last = getattr(self, "_last_announced", None)
        if last and now - last[1] < 30:
            return last[0]
        return item.name or "uploaded file"

    # ================================================================ responses

    def response(self, flow: http.HTTPFlow) -> None:
        resp = flow.response
        if resp is None or resp.headers.get("x-upload-guard"):
            return
        site = sites.site_of_host(flow.request.pretty_host)
        if not site:
            return
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype:
            self._learn_upload_targets(flow)
        elif "text/html" in ctype and resp.status_code == 200 and self.settings.response_color_enabled:
            self._inject_style(flow)

    def _learn_upload_targets(self, flow: http.HTTPFlow) -> None:
        body = flow.response.get_content(strict=False) or b""
        announced = payload.file_names_announced(flow.request.get_content(strict=False) or b"")
        for url in payload.urls_in(body):
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            if host and sites.is_storage_host(host) and host not in self.learned_hosts:
                log.info("learned upload host %s from %s", host, flow.request.pretty_host)
                self.learned_hosts.add(host)
            if announced and sites.is_storage_host(host):
                self._names_by_path[parts.path] = (announced[0][0], time.monotonic())

    def _inject_style(self, flow: http.HTTPFlow) -> None:
        html = flow.response.get_text(strict=False)
        if not html or STYLE_ID in html:
            return
        style = f'<style id="{STYLE_ID}">{RESPONSE_CSS % self.settings.response_color}</style>'
        match = re.search(r"</head\s*>", html, re.I)
        html = html[:match.start()] + style + html[match.start():] if match else style + html
        flow.response.set_text(html)
        csp = flow.response.headers.get("content-security-policy", "")
        if csp:
            flow.response.headers["content-security-policy"] = _allow_style(csp, style)


def _allow_style(csp: str, style_tag: str) -> str:
    """If the page's CSP restricts inline styles, allow exactly our style block (by hash)."""
    import base64
    css = style_tag[style_tag.index(">") + 1: style_tag.rindex("<")]
    digest = "'sha256-" + base64.b64encode(hashlib.sha256(css.encode()).digest()).decode() + "'"
    out = []
    for directive in csp.split(";"):
        parts = directive.strip().split()
        if parts and parts[0] in ("style-src", "style-src-elem") and "'unsafe-inline'" not in parts:
            parts.append(digest)
        out.append(" ".join(parts))
    return "; ".join(p for p in out if p)
