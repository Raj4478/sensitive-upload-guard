"""Command line: ``python -m upload_guard <command>``.

  install         register the desktop app with Chrome, Edge and Brave
  uninstall       remove that registration
  check FILE...   inspect files from the terminal (no browser needed)
  dialog-test     show the native confirmation dialog once
  host            run the native-messaging host (the browser does this)
"""

from __future__ import annotations

import argparse
import mimetypes
import sys
from pathlib import Path

from upload_guard import __version__, dialog, inspector, installer
from upload_guard.config import app_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="upload_guard", description="Sensitive Upload Guard desktop app")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    inst = sub.add_parser("install", help="register with Chrome / Edge / Brave")
    inst.add_argument("--extension-id", default=installer.EXTENSION_ID)
    sub.add_parser("uninstall", help="remove the registration")
    check = sub.add_parser("check", help="inspect files for the marker")
    check.add_argument("files", nargs="+")
    sub.add_parser("dialog-test", help="show the confirmation dialog once")
    sub.add_parser("host", help="native-messaging host (started by the browser)")
    args = parser.parse_args(argv)

    if args.command == "install":
        for line in installer.install(args.extension_id):
            print("registered", line)
        print(f"\nSettings and logs: {app_dir()}")
        print("Next: load the 'extension' folder in chrome://extensions (Developer mode > Load unpacked).")
        return 0
    if args.command == "uninstall":
        removed = installer.uninstall()
        print("removed:", ", ".join(removed) or "nothing")
        return 0
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
        choice = dialog.confirm("Sensitive content detected - Upload Guard",
                                "This is a test of the native confirmation dialog.\n\n"
                                "Continue: allow the action.\nCancel: block it.", timeout=60)
        print(choice)
        return 0
    if args.command == "host":
        from upload_guard.host import main as host_main
        return host_main()
    return 2


if __name__ == "__main__":
    sys.exit(main())
