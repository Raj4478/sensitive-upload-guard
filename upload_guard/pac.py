"""Tell browsers to send only ChatGPT/Perplexity traffic through the proxy.

A proxy auto-config (PAC) file is a small JavaScript function every browser
asks for each connection: "which proxy for this host?". Ours answers "the local
proxy" for the two sites and the storage services they upload to, and "DIRECT"
for everything else - so normal browsing never touches Upload Guard.

For the protected sites there is no DIRECT fallback: if Upload Guard is not
running, those two sites do not load (fail closed) rather than load unprotected.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from upload_guard import sites

PAC_PATH = "/upload-guard.pac"


def pac_script(proxy_port: int) -> str:
    site_domains = ["chatgpt.com", "chat.openai.com", "perplexity.ai"]
    storage = [s.lstrip(".") for s in sites.STORAGE_SUFFIXES]
    test_hosts = [h for h in __import__("os").environ.get("UPLOAD_GUARD_TEST_HOSTS", "").split(",") if h]
    checks = " ||\n      ".join(f'host == "{d}" || dnsDomainIs(host, ".{d}")' for d in site_domains + storage)
    tests = "".join(f'\n  if (host == "{h}") return proxy;' for h in test_hosts)
    return f"""// Upload Guard: only ChatGPT, Perplexity and their upload storage go through the local proxy.
function FindProxyForURL(url, host) {{
  host = host.toLowerCase();
  var proxy = "PROXY 127.0.0.1:{proxy_port}";{tests}
  if ({checks}) return proxy;
  return "DIRECT";
}}
"""


class PacServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, proxy_port: int):
        self.script = pac_script(proxy_port).encode()
        super().__init__(("127.0.0.1", port), _Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}{PAC_PATH}"

    def start(self) -> None:
        threading.Thread(target=self.serve_forever, name="pac", daemon=True).start()


class _Handler(BaseHTTPRequestHandler):
    server: PacServer

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != PAC_PATH:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ns-proxy-autoconfig")
        self.send_header("Content-Length", str(len(self.server.script)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(self.server.script)
