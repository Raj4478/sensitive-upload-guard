"""Command line: ``python -m upload_guard [command]``.

  (no command)    open the Upload Guard desktop app
  check FILE...   inspect files from the terminal
  dialog-test     show the native confirmation dialog once
  proxy           run the inspecting proxy without the window (used by tests)
  browser         open a protected browser window (no system changes)
"""

from __future__ import annotations

import argparse
import mimetypes
import sys
import threading
from pathlib import Path

from upload_guard import __version__, dialog, inspector
from upload_guard.config import Settings, app_dir, setup_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="upload_guard", description="Sensitive Upload Guard desktop DLP")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    check = sub.add_parser("check", help="inspect files for the marker")
    check.add_argument("files", nargs="+")
    sub.add_parser("dialog-test", help="show the confirmation dialog once")
    proxy = sub.add_parser("proxy", help="run the proxy without the window")
    proxy.add_argument("--port", type=int)
    proxy.add_argument("--pac-port", type=int)
    sub.add_parser("browser", help="open a protected browser window")
    args = parser.parse_args(argv)

    if args.command is None:
        from upload_guard.app import run_app
        return run_app()
    if args.command == "check":
        exit_code = 0
        for name in args.files:
            path = Path(name)
            finding = inspector.inspect_file(path.name, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "")
            status = "MARKER FOUND" if finding.detected else ("could not read" if not finding.inspected else "clean")
            print(f"{path.name}: {status} [{finding.kind}] {finding.location} {finding.snippet}".rstrip())
            for err in finding.errors:
                print(f"   note: {err}")
            exit_code = exit_code or int(finding.detected)
        return exit_code
    if args.command == "dialog-test":
        print(dialog.confirm(dialog.Content(
            window_title="Upload Guard · test", heading="This is a test of the confirmation dialog",
            body="When WALLED_SENSITIVE is found in a prompt or file on ChatGPT or Perplexity, "
                 "this dialog asks before anything is sent.",
            continue_note="Allow the action", cancel_note="Block the action"), timeout=60))
        return 0
    if args.command == "proxy":
        return _headless(args.port, args.pac_port)
    if args.command == "browser":
        from upload_guard import browser
        settings = Settings.load()
        browser.open_protected_window(f"http://127.0.0.1:{settings.pac_port}/upload-guard.pac")
        print("Opened. Keep Upload Guard running while you use that window.")
        return 0
    return 2


def _headless(port: int | None, pac_port: int | None) -> int:
    from upload_guard.decisions import DecisionCenter
    from upload_guard.pac import PacServer
    from upload_guard.proxy_server import ProxyServer

    setup_logging()
    settings = Settings.load()
    if port:
        settings.proxy_port = port
    if pac_port:
        settings.pac_port = pac_port
    center = DecisionCenter(settings, on_event=lambda e: print(f"[{e.site}] {e.source} {e.name!r}: {e.decision} ({e.detail})", flush=True))
    server = ProxyServer(settings, center)
    server.start()
    pac = PacServer(settings.pac_port, settings.proxy_port)
    pac.start()
    from upload_guard import ca
    print(f"Upload Guard proxy on 127.0.0.1:{settings.proxy_port}; PAC at {pac.url}", flush=True)
    print(f"CA public-key hash: {ca.spki_hash()}", flush=True)
    print(f"Data folder: {app_dir()}  (Ctrl+C to stop)", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
