"""One decision per submission, shared by every request that carries it.

A single "send" can produce several requests with the same sensitive text: the
real prompt, a retry, title generation, analytics beacons. Without coordination
the user would see several dialogs. The DecisionCenter:

* shows **one** dialog per distinct submission (same key while it is open, and
  for a few seconds afterwards, reuses that answer);
* remembers **approved** text for a while, so tracking beacons that repeat an
  approved prompt go through, and everything else carrying the marker does not;
* lets tracking requests **wait** while a dialog is open on the same site,
  then decide based on the answer.

All methods run on the proxy's asyncio loop; the dialog itself runs in a worker
thread so many requests can wait at once.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from upload_guard import detector, dialog
from upload_guard.config import Settings, app_dir

log = logging.getLogger(__name__)

# Requests carrying the same content share the dialog that is open for it. After
# it closes, a duplicate that arrives within this short window (e.g. an automatic
# retry of the same send) reuses the answer; anything later is a new attempt by
# the user and gets a new dialog, so Cancel never blocks a deliberate resend.
SAME_SUBMISSION_SECONDS = {"continue": 3.0, "cancel": 1.0}
APPROVAL_MEMORY_SECONDS = 600    # approved text that tracking beacons may repeat


@dataclass
class Event:
    """One line in the app's activity list and the audit log."""
    time: float
    site: str
    source: str          # "prompt" | "file" | "tracking"
    name: str            # file name or a short prompt preview
    detail: str          # where the marker was found / why
    decision: str        # "allow" | "continue" | "cancel" | "blocked"
    sha256: str = ""


class DecisionCenter:
    def __init__(self, settings: Settings, on_event: Callable[[Event], None] | None = None):
        self.settings = settings
        self.on_event = on_event
        self._pending: dict[str, tuple[asyncio.Future, str]] = {}
        self._recent: dict[str, tuple[str, float]] = {}
        self._approved: list[tuple[str, str, float]] = []     # (site, folded text, time)
        self._audit_lock = threading.Lock()

    # ------------------------------------------------------------------ asking

    async def ask(self, key: str, site: str, content: dialog.Content) -> str:
        """Return "continue" or "cancel", showing at most one dialog per key."""
        now = time.monotonic()
        self._recent = {k: v for k, v in self._recent.items()
                        if now - v[1] < SAME_SUBMISSION_SECONDS.get(v[0], 1.0)}
        if key in self._recent:
            return self._recent[key][0]
        if key in self._pending:
            return await asyncio.shield(self._pending[key][0])
        future = asyncio.get_running_loop().create_future()
        self._pending[key] = (future, site)
        try:
            decision = await asyncio.to_thread(dialog.confirm, content, self.settings.dialog_timeout_seconds)
        except Exception:
            log.exception("dialog failed; cancelling")
            decision = dialog.CANCEL
        finally:
            self._pending.pop(key, None)
        self._recent[key] = (decision, time.monotonic())
        future.set_result(decision)
        return decision

    def busy(self, site: str) -> bool:
        return any(s == site for _, s in self._pending.values())

    async def wait_until_idle(self, site: str) -> None:
        """Hold a tracking request while a dialog for the same site is open."""
        deadline = time.monotonic() + self.settings.dialog_timeout_seconds + 5
        while self.busy(site) and time.monotonic() < deadline:
            await asyncio.sleep(0.2)

    # ------------------------------------------------------------------ approved text

    def approve(self, site: str, text: str) -> None:
        folded = detector.fold(text)
        if folded:
            self._approved.append((site, folded, time.monotonic()))

    def was_approved(self, site: str, body_text: str) -> bool:
        """True if this request only repeats text the user already approved."""
        now = time.monotonic()
        self._approved = [a for a in self._approved if now - a[2] < APPROVAL_MEMORY_SECONDS]
        folded = detector.fold(body_text)
        return any(s == site and len(t) >= len("WALLEDSENSITIVE") and t in folded for s, t, _ in self._approved) \
            or any(s == site and folded and folded in t for s, t, _ in self._approved)

    # ------------------------------------------------------------------ reporting

    def record(self, event: Event) -> None:
        log.info("%s %s %s -> %s (%s)", event.site, event.source, event.name, event.decision, event.detail)
        if self.settings.audit_log:
            entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(event.time)), "site": event.site,
                     "source": event.source, "name": event.name, "detail": event.detail,
                     "decision": event.decision, "sha256": event.sha256}
            with self._audit_lock:
                try:
                    with (app_dir() / "audit.log").open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except OSError:
                    log.warning("could not write the audit log")
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                log.exception("activity listener failed")
