"""Run the inspecting proxy (mitmproxy) on a background thread.

mitmproxy is the network engine: it terminates TLS for the hosts the add-on
chooses, speaks HTTP/1.1, HTTP/2 and WebSockets, and calls our hooks. It runs
its own asyncio loop on this thread so the desktop window stays responsive.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from upload_guard import ca
from upload_guard.addon import GuardAddon
from upload_guard.config import Settings
from upload_guard.decisions import DecisionCenter

log = logging.getLogger(__name__)


class ProxyServer:
    def __init__(self, settings: Settings, center: DecisionCenter, port: int | None = None):
        self.settings = settings
        self.center = center
        self.port = port or settings.proxy_port
        self.addon = GuardAddon(settings, center)
        self._master = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread: threading.Thread | None = None

    def start(self, timeout: float = 20) -> None:
        confdir = ca.ensure_ca()
        self._thread = threading.Thread(target=self._run, args=(str(confdir),), name="proxy", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("the proxy did not start in time")
        if self._error:
            raise RuntimeError(f"the proxy could not start: {self._error}")
        log.info("proxy listening on 127.0.0.1:%s", self.port)

    def _run(self, confdir: str) -> None:
        try:
            asyncio.run(self._main(confdir))
        except BaseException as exc:      # report start-up failures (e.g. port in use)
            self._error = exc
            self._ready.set()

    async def _main(self, confdir: str) -> None:
        from mitmproxy import options
        from mitmproxy.tools.dump import DumpMaster

        opts = options.Options(listen_host="127.0.0.1", listen_port=self.port, confdir=confdir)
        self._master = DumpMaster(opts, with_termlog=False, with_dumper=False)
        self._master.addons.add(self.addon)
        self._loop = asyncio.get_running_loop()
        self._loop.call_later(0.5, self._ready.set)
        await self._master.run()

    def stop(self) -> None:
        if self._master and self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._master.shutdown)
        if self._thread:
            self._thread.join(timeout=10)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._error)
