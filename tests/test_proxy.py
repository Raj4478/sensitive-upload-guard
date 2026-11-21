"""Tests for the network side: request parsing, site rules, decisions and the proxy add-on.

The add-on is exercised with mitmproxy's own test flows, so these run without a
browser, a network connection or any system setting. The dialog is answered by
UPLOAD_GUARD_AUTO_ANSWER.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mitmproxy.test import tflow, tutils

from upload_guard import ca, decisions, dialog, pac, payload, sites
from upload_guard.addon import GuardAddon, _allow_style
from upload_guard.config import Settings
from upload_guard.decisions import DecisionCenter

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_GUARD_HOME", str(tmp_path / "home"))


def make_flow(method="POST", host="chatgpt.com", path="/backend-api/f/conversation", body=b"", ctype="application/json",
              origin=None):
    flow = tflow.tflow()
    req = flow.request
    req.method, req.host, req.port, req.scheme, req.path = method, host, 443, "https", path
    req.headers["host"] = host
    req.headers["content-type"] = ctype
    if origin:
        req.headers["origin"] = origin
    req.content = body
    return flow


def addon_and_center(answer, monkeypatch, settings=None):
    monkeypatch.setenv("UPLOAD_GUARD_AUTO_ANSWER", answer)
    settings = settings or Settings()
    center = DecisionCenter(settings)
    return GuardAddon(settings, center), center


# ============================================================================ payload parsing

def test_json_prompt_picks_the_string_with_the_marker():
    body = json.dumps({"action": "next", "messages": [{"content": {"parts": ["Summarise WALLED_SENSITIVE roadmap"]}}],
                       "model": "auto"}).encode()
    parsed = payload.parse(body, "application/json")
    assert parsed.has_marker_text and parsed.prompt == "Summarise WALLED_SENSITIVE roadmap" and not parsed.files


def test_form_encoded_with_json_inside():
    inner = json.dumps({"text": "hello WALLED_SENSITIVE"})
    body = ("state=1&payload=" + __import__("urllib.parse").parse.quote(inner)).encode()
    parsed = payload.parse(body, "application/x-www-form-urlencoded;charset=UTF-8")
    assert parsed.prompt == "hello WALLED_SENSITIVE"


def test_multipart_files_keep_their_names():
    boundary = "XyZ"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"key\"\r\n\r\nuploads/1\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"report.pdf\"\r\n"
            f"Content-Type: application/pdf\r\n\r\n").encode() + b"%PDF-1.4 data" + f"\r\n--{boundary}--\r\n".encode()
    parsed = payload.parse(body, f"multipart/form-data; boundary={boundary}")
    assert [(f.name, f.data, f.mime) for f in parsed.files] == [("report.pdf", b"%PDF-1.4 data", "application/pdf")]


def test_raw_upload_is_one_file():
    parsed = payload.parse(b"\x89PNG\r\n\x1a\n...", "image/png", "/container/file-abc123?sig=x")
    assert len(parsed.files) == 1 and parsed.files[0].name == "file-abc123"


def test_announced_names_and_urls():
    assert payload.file_names_announced(b'{"file_name": "a.pdf", "file_size": 42, "use_case": "x"}') == [("a.pdf", 42)]
    assert payload.urls_in(b'{"upload_url": "https:\\/\\/x.blob.core.windows.net\\/c\\/f?sig=1"}') == \
        ["https://x.blob.core.windows.net/c/f?sig=1"]


# ============================================================================ site rules

def test_site_and_tracking_rules():
    assert sites.site_of_host("chatgpt.com") == "ChatGPT" and sites.site_of_host("www.perplexity.ai") == "Perplexity"
    assert sites.site_of_host("google.com") is None and sites.site_of_host("notchatgpt.com") is None
    assert sites.site_of_origin("https://chatgpt.com/c/123") == "ChatGPT"
    assert sites.is_tracking("www.perplexity.ai", "/rest/event/analytics")
    assert sites.is_tracking("browser-intake-datadoghq.com", "/api/v2/rum")
    assert not sites.is_tracking("www.perplexity.ai", "/rest/sse/perplexity_ask")
    assert not sites.is_tracking("chatgpt.com", "/backend-api/f/conversation")


def test_pac_routes_only_the_protected_sites():
    script = pac.pac_script(8899)
    assert 'dnsDomainIs(host, ".chatgpt.com")' in script and 'dnsDomainIs(host, ".perplexity.ai")' in script
    assert 'return "DIRECT"' in script and "PROXY 127.0.0.1:8899" in script


def test_only_protected_hosts_are_decrypted(monkeypatch):
    addon, _ = addon_and_center("cancel", monkeypatch)
    assert addon.should_decrypt("chatgpt.com") and addon.should_decrypt("www.perplexity.ai")
    assert addon.should_decrypt("files.oaiusercontent.com")         # the site's own storage
    assert not addon.should_decrypt("www.google.com")
    assert not addon.should_decrypt("random-bucket.s3.amazonaws.com")   # unrelated storage stays encrypted
    addon.learned_hosts.add("random-bucket.s3.amazonaws.com")           # ...unless a protected site named it
    assert addon.should_decrypt("random-bucket.s3.amazonaws.com")


# ============================================================================ the add-on

@pytest.mark.parametrize("answer, blocked", [("cancel", True), ("continue", False)])
def test_prompt_with_marker_is_held_and_decided(monkeypatch, answer, blocked):
    addon, _ = addon_and_center(answer, monkeypatch)
    flow = make_flow(body=json.dumps({"messages": [{"content": {"parts": ["x WALLED_SENSITIVE y"]}}]}).encode())
    asyncio.run(addon.request(flow))
    assert (flow.response is not None and flow.response.status_code == 403) is blocked


def test_clean_prompt_is_untouched(monkeypatch):
    addon, _ = addon_and_center("cancel", monkeypatch)
    flow = make_flow(body=b'{"query_str": "who wrote Pride and Prejudice?"}')
    asyncio.run(addon.request(flow))
    assert flow.response is None


def test_get_requests_are_never_inspected(monkeypatch):
    addon, _ = addon_and_center("cancel", monkeypatch)
    flow = make_flow(method="GET", path="/search?q=WALLED_SENSITIVE", body=b"")
    asyncio.run(addon.request(flow))
    assert flow.response is None


def test_raw_upload_to_storage_named_from_announcement(monkeypatch):
    addon, center = addon_and_center("cancel", monkeypatch)
    events = []
    center.on_event = events.append
    data = (SAMPLES / "sensitive.pdf").read_bytes()
    announce = make_flow(path="/backend-api/files", body=json.dumps({"file_name": "board.pdf", "file_size": len(data)}).encode())
    asyncio.run(addon.request(announce))
    upload = make_flow(method="PUT", host="sdmntpr.blob.core.windows.net", path="/files/file-9f2?sig=abc",
                       body=data, ctype="application/pdf", origin="https://chatgpt.com")
    asyncio.run(addon.request(upload))
    assert upload.response.status_code == 403
    assert events[-1].name == "board.pdf" and events[-1].detail == "page 2"


def test_clean_file_uploads(monkeypatch):
    addon, _ = addon_and_center("cancel", monkeypatch)
    flow = make_flow(method="PUT", host="x.blob.core.windows.net", path="/f/1", body=(SAMPLES / "clean.pdf").read_bytes(),
                     ctype="application/pdf", origin="https://chatgpt.com")
    asyncio.run(addon.request(flow))
    assert flow.response is None


def test_uploads_from_other_sites_are_ignored(monkeypatch):
    addon, _ = addon_and_center("cancel", monkeypatch)
    flow = make_flow(method="PUT", host="x.blob.core.windows.net", path="/f/1", body=b"WALLED_SENSITIVE",
                     ctype="text/plain", origin="https://some-other-app.com")
    asyncio.run(addon.request(flow))
    assert flow.response is None


def test_tracking_beacon_blocked_unless_text_was_approved(monkeypatch):
    addon, center = addon_and_center("cancel", monkeypatch)
    beacon = lambda: make_flow(host="www.perplexity.ai", path="/rest/event/analytics",  # noqa: E731
                               body=b'{"event_data": {"query": "draft WALLED_SENSITIVE email"}}')
    first = beacon()
    asyncio.run(addon.request(first))
    assert first.response.status_code == 204           # unapproved: dropped, no dialog
    center.approve("Perplexity", "draft WALLED_SENSITIVE email")
    second = beacon()
    asyncio.run(addon.request(second))
    assert second.response is None                      # approved text: allowed


def test_html_pages_get_the_colour_style(monkeypatch):
    addon, _ = addon_and_center("cancel", monkeypatch)
    flow = make_flow(method="GET", path="/", body=b"")
    flow.response = tutils.tresp(content=b"<html><head><title>x</title></head><body></body></html>")
    flow.response.headers["content-type"] = "text/html; charset=utf-8"
    addon.response(flow)
    html = flow.response.get_text()
    assert 'id="upload-guard-response-colour"' in html and html.index("upload-guard") < html.index("</head>")
    assert "#0ea5e9" in html


def test_csp_without_unsafe_inline_gets_our_style_hash():
    csp = "default-src 'self'; style-src 'self' 'nonce-abc'; script-src 'self'"
    out = _allow_style(csp, "<style id=x>a{color:red}</style>")
    assert "'sha256-" in out.split("style-src")[1].split(";")[0]
    untouched = _allow_style("style-src 'self' 'unsafe-inline'", "<style>a{}</style>")
    assert "sha256" not in untouched          # adding a hash would disable 'unsafe-inline'


# ============================================================================ decisions

def test_one_dialog_for_duplicate_requests(monkeypatch):
    calls = []

    def fake_confirm(content, timeout):
        calls.append(content.heading)
        import time
        time.sleep(0.3)
        return "continue"

    monkeypatch.setattr(dialog, "confirm", fake_confirm)
    center = DecisionCenter(Settings())
    content = dialog.Content("t", "h", "b", "c", "x")

    async def both():
        return await asyncio.gather(center.ask("k", "ChatGPT", content), center.ask("k", "ChatGPT", content))

    assert asyncio.run(both()) == ["continue", "continue"] and len(calls) == 1


def test_resend_after_cancel_asks_again(monkeypatch):
    answers = iter(["cancel", "continue"])
    monkeypatch.setattr(dialog, "confirm", lambda content, timeout: next(answers))
    monkeypatch.setattr(decisions, "SAME_SUBMISSION_SECONDS", {"continue": 3.0, "cancel": 0.0})
    center = DecisionCenter(Settings())
    content = dialog.Content("t", "h", "b", "c", "x")
    assert asyncio.run(center.ask("k", "ChatGPT", content)) == "cancel"
    assert asyncio.run(center.ask("k", "ChatGPT", content)) == "continue"


# ============================================================================ local CA

def test_ca_is_created_once_with_our_name():
    folder = ca.ensure_ca()
    first = (folder / "mitmproxy-ca.pem").read_bytes()
    assert ca.ensure_ca() == folder and (folder / "mitmproxy-ca.pem").read_bytes() == first
    assert b"BEGIN RSA PRIVATE KEY" in first and b"BEGIN CERTIFICATE" in first
    assert len(ca.thumbprint()) == 40 and len(ca.spki_hash()) == 44
    from cryptography import x509
    cert = x509.load_der_x509_certificate((folder / "mitmproxy-ca-cert.cer").read_bytes())
    assert ca.COMMON_NAME in cert.subject.rfc4514_string()
