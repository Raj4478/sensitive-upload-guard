"""The Upload Guard desktop window.

Starts the inspecting proxy, walks the user through the two one-time setup
steps, and shows what was checked, flagged and blocked. Closing the window turns
protection off (restoring the previous proxy setting) and stops the proxy.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from upload_guard import __version__, browser, system
from upload_guard.config import Settings, app_dir, setup_logging
from upload_guard.decisions import DecisionCenter, Event
from upload_guard.pac import PacServer
from upload_guard.proxy_server import ProxyServer

log = logging.getLogger(__name__)

INK, INK_2 = "#0f172a", "#1e293b"
BG, CARD, LINE = "#f8fafc", "#ffffff", "#e2e8f0"
TEXT, MUTED = "#0f172a", "#64748b"
OK, WARN, BAD, ACCENT = "#10b981", "#f59e0b", "#f43f5e", "#0ea5e9"
FONT = "Segoe UI" if sys.platform == "win32" else "Helvetica"
SWATCHES = ["#0ea5e9", "#10b981", "#8b5cf6", "#f59e0b", "#f43f5e"]


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = Settings.load()
        self.events: queue.Queue[Event] = queue.Queue()
        self.center = DecisionCenter(self.settings, on_event=self.events.put)
        self.proxy = ProxyServer(self.settings, self.center)
        self.pac: PacServer | None = None
        self.stats = {"checked": 0, "flagged": 0, "blocked": 0}
        self._build()
        self._start_engine()
        self.root.after(200, self._pump)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

    # ================================================================ engine

    def _start_engine(self) -> None:
        try:
            self.proxy.start()
            self.pac = PacServer(self.settings.pac_port, self.settings.proxy_port)
            self.pac.start()
            self.engine_ok = True
        except Exception as exc:
            log.exception("engine failed to start")
            self.engine_ok = False
            self.engine_error = str(exc)
        self.refresh()

    @property
    def pac_url(self) -> str:
        return f"http://127.0.0.1:{self.settings.pac_port}/upload-guard.pac"

    # ================================================================ layout

    def _build(self) -> None:
        root = self.root
        root.title("Upload Guard")
        root.configure(bg=BG)
        scale = root.winfo_fpixels("1i") / 96          # 1.0 at 100 %, 1.5 at 150 % display scaling
        height = min(int(760 * scale), root.winfo_screenheight() - int(90 * scale))
        root.geometry(f"{int(560 * scale)}x{height}+{int(60 * scale)}+{int(20 * scale)}")
        root.minsize(int(520 * scale), int(560 * scale))
        try:
            self._icon = tk.PhotoImage(file=str(Path(__file__).parent / "assets" / "icon.png"))
            root.iconphoto(True, self._icon)
            self._logo = self._icon.subsample(4, 4)
        except tk.TclError:
            self._logo = None

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Treeview", rowheight=24, font=(FONT, 9), background=CARD, fieldbackground=CARD,
                        bordercolor=LINE, borderwidth=0)
        style.configure("Treeview.Heading", font=(FONT, 9, "bold"), background=BG, foreground=MUTED, relief="flat")

        header = tk.Frame(root, bg=INK, padx=20, pady=16)
        header.pack(fill="x")
        top = tk.Frame(header, bg=INK)
        top.pack(fill="x")
        if self._logo:
            tk.Label(top, image=self._logo, bg=INK).pack(side="left", padx=(0, 10))
        titles = tk.Frame(top, bg=INK)
        titles.pack(side="left")
        tk.Label(titles, text="Upload Guard", font=(FONT, 15, "bold"), fg="#f8fafc", bg=INK).pack(anchor="w")
        tk.Label(titles, text="Sensitive content protection for ChatGPT and Perplexity", font=(FONT, 9),
                 fg="#94a3b8", bg=INK).pack(anchor="w")
        self.pill = tk.Label(header, text="  Starting…  ", font=(FONT, 9, "bold"), fg="#f8fafc", bg=INK_2, padx=10, pady=4)
        self.pill.pack(anchor="w", pady=(12, 0))

        body = tk.Frame(root, bg=BG, padx=16, pady=12)
        body.pack(fill="both", expand=True)

        # Setup steps
        setup = self._card(body, "Setup")
        self.step_engine = self._step(setup, "Inspection engine", "")
        self.step_cert = self._step(setup, "Certificate trusted", "Install certificate", self.on_install_cert)
        self.step_route = self._step(setup, "Browser traffic routed", "Turn protection on", self.on_toggle_routing)
        alt = tk.Frame(setup, bg=CARD)
        alt.pack(fill="x", pady=(8, 0))
        tk.Label(alt, text="No setup? Open a protected window instead:", font=(FONT, 9), fg=MUTED, bg=CARD).pack(side="left")
        self._button(alt, "Open protected browser", self.on_open_browser, secondary=True).pack(side="right")

        # Stats
        stats = self._card(body, "Today")
        row = tk.Frame(stats, bg=CARD)
        row.pack(fill="x")
        self.stat_labels = {}
        for key, color in (("checked", TEXT), ("flagged", WARN), ("blocked", BAD)):
            cell = tk.Frame(row, bg=BG, padx=8, pady=6)
            cell.pack(side="left", expand=True, fill="x", padx=4)
            self.stat_labels[key] = tk.Label(cell, text="0", font=(FONT, 16, "bold"), fg=color, bg=BG)
            self.stat_labels[key].pack()
            tk.Label(cell, text=key.title(), font=(FONT, 9), fg=MUTED, bg=BG).pack()

        # Response colour
        colour = self._card(body, "AI response colour")
        crow = tk.Frame(colour, bg=CARD)
        crow.pack(fill="x")
        self.colour_on = tk.BooleanVar(value=self.settings.response_color_enabled)
        tk.Checkbutton(crow, text="Recolour AI responses", variable=self.colour_on, command=self.on_colour_change,
                       font=(FONT, 10), bg=CARD, activebackground=CARD).pack(side="left")
        self.swatch_buttons = []
        for colour_hex in reversed(SWATCHES):
            b = tk.Label(crow, bg=colour_hex, width=2, height=1, cursor="hand2", relief="flat",
                         highlightthickness=2, highlightbackground=CARD)
            b.pack(side="right", padx=3)
            b.bind("<Button-1>", lambda _e, c=colour_hex: self.on_colour_change(c))
            self.swatch_buttons.append((colour_hex, b))
        self._paint_swatches()

        # Activity
        activity = self._card(body, "Activity", expand=True)
        cols = ("time", "site", "what", "name", "decision")
        self.tree = ttk.Treeview(activity, columns=cols, show="headings", height=8)
        for col, width in zip(cols, (64, 80, 64, 190, 76)):
            self.tree.heading(col, text=col.title())
            self.tree.column(col, width=width, anchor="w")
        self.tree.tag_configure("allow", foreground=MUTED)
        self.tree.tag_configure("continue", foreground="#b45309")
        self.tree.tag_configure("cancel", foreground=BAD)
        self.tree.tag_configure("blocked", foreground=BAD)
        self.tree.pack(fill="both", expand=True)

        footer = tk.Frame(root, bg=BG, padx=20, pady=8)
        footer.pack(fill="x")
        link = tk.Label(footer, text="Open logs folder", font=(FONT, 9, "underline"), fg=ACCENT, bg=BG, cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda _e: self.open_folder())
        tk.Label(footer, text=f"Marker WALLED_SENSITIVE  ·  v{__version__}", font=(FONT, 9), fg=MUTED, bg=BG).pack(side="right")

    def _card(self, parent, title: str, expand: bool = False) -> tk.Frame:
        outer = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=LINE, padx=14, pady=10)
        outer.pack(fill="both" if expand else "x", expand=expand, pady=6)
        tk.Label(outer, text=title.upper(), font=(FONT, 8, "bold"), fg=MUTED, bg=CARD).pack(anchor="w", pady=(0, 6))
        return outer

    def _button(self, parent, text, command, secondary=False) -> tk.Button:
        return tk.Button(parent, text=text, command=command, font=(FONT, 9, "bold"), relief="flat", cursor="hand2",
                         padx=12, pady=4, bd=0,
                         bg=BG if secondary else INK, fg=TEXT if secondary else "#ffffff",
                         activebackground=LINE if secondary else INK_2, activeforeground=TEXT if secondary else "#ffffff")

    def _step(self, parent, label: str, button_text: str, command=None):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=4)
        mark = tk.Label(row, text="○", font=(FONT, 13, "bold"), fg=MUTED, bg=CARD, width=2)
        mark.pack(side="left", anchor="n")
        button = self._button(row, button_text, command) if command else None
        if button:
            button.configure(width=20)
            button.pack(side="right", anchor="center")
        words = tk.Frame(row, bg=CARD)
        words.pack(side="left", fill="x", expand=True)
        tk.Label(words, text=label, font=(FONT, 10), fg=TEXT, bg=CARD).pack(anchor="w")
        detail = tk.Label(words, text="", font=(FONT, 8), fg=MUTED, bg=CARD)
        detail.pack(anchor="w")
        return {"mark": mark, "detail": detail, "button": button}

    # ================================================================ state

    def refresh(self) -> None:
        engine = getattr(self, "engine_ok", False)
        cert = system.certificate_installed() if engine else False
        routed = system.routing_enabled(self.pac_url) if engine else False
        self._set_step(self.step_engine, engine,
                       f"127.0.0.1:{self.settings.proxy_port}" if engine else getattr(self, "engine_error", "starting"))
        self._set_step(self.step_cert, cert, "trusted for this user" if cert else "one click, Windows asks to confirm",
                       button_text="Remove" if cert else "Install certificate")
        self._set_step(self.step_route, routed, "ChatGPT and Perplexity only" if routed else "off",
                       button_text="Turn protection off" if routed else "Turn protection on")
        self.step_route["button"].configure(state="normal" if (cert or routed) else "disabled")
        if sys.platform == "darwin" and not getattr(self, "_mac_poll", False):
            # On macOS the user turns routing on in System Settings: keep the status current.
            self._mac_poll = True

            def poll():
                self.refresh()
                self.root.after(4000, poll)
            self.root.after(4000, poll)
        if engine and cert and routed:
            self.pill.configure(text="  ●  Protected  ", bg="#065f46")
        elif engine:
            self.pill.configure(text="  ●  Not protecting yet  ", bg="#92400e")
        else:
            self.pill.configure(text="  ●  Engine stopped  ", bg="#9f1239")

    @staticmethod
    def _set_step(step, done: bool, detail: str, button_text: str | None = None) -> None:
        step["mark"].configure(text="✓" if done else "○", fg=OK if done else MUTED)
        step["detail"].configure(text=detail)
        if step["button"] is not None and button_text:
            step["button"].configure(text=button_text)

    def _pump(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._add_event(event)
        except queue.Empty:
            pass
        self.root.after(200, self._pump)

    def _add_event(self, event: Event) -> None:
        self.stats["checked"] += 1
        if event.decision in ("continue", "cancel", "blocked"):
            self.stats["flagged"] += 1
        if event.decision in ("cancel", "blocked"):
            self.stats["blocked"] += 1
        for key, label in self.stat_labels.items():
            label.configure(text=str(self.stats[key]))
        shown = {"allow": "clean", "continue": "allowed", "cancel": "blocked", "blocked": "blocked"}[event.decision]
        self.tree.insert("", 0, values=(time.strftime("%H:%M:%S", time.localtime(event.time)), event.site,
                                        event.source, event.name, shown), tags=(event.decision,))
        for extra in self.tree.get_children()[200:]:
            self.tree.delete(extra)

    # ================================================================ actions

    def _in_background(self, work, done) -> None:
        def run():
            result = work()
            self.root.after(0, lambda: done(result))
        threading.Thread(target=run, daemon=True).start()

    def on_install_cert(self) -> None:
        if system.certificate_installed():
            if system.routing_enabled(self.pac_url):
                system.disable_routing(self.pac_url)
            self._in_background(system.remove_certificate, lambda _r: self.refresh())
        else:
            self._in_background(system.install_certificate, lambda _r: self.refresh())

    def on_toggle_routing(self) -> None:
        if system.routing_enabled(self.pac_url):
            ok, message = system.disable_routing(self.pac_url)
        else:
            ok, message = system.enable_routing(self.pac_url)
        if not ok:
            # macOS: the user flips the setting in System Settings; show exactly how.
            from tkinter import messagebox
            messagebox.showinfo("Upload Guard", message, parent=self.root)
        self.refresh()

    def on_open_browser(self) -> None:
        try:
            browser.open_protected_window(self.pac_url)
        except FileNotFoundError as exc:
            from tkinter import messagebox
            messagebox.showerror("Upload Guard", str(exc), parent=self.root)

    def on_colour_change(self, colour: str | None = None) -> None:
        if colour:
            self.settings.response_color = colour
        self.settings.response_color_enabled = self.colour_on.get()
        self.settings.save()
        self._paint_swatches()

    def _paint_swatches(self) -> None:
        for colour_hex, button in self.swatch_buttons:
            button.configure(highlightbackground=TEXT if colour_hex == self.settings.response_color else CARD)

    def open_folder(self) -> None:
        folder = str(app_dir())
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])

    def quit(self) -> None:
        # Leaving the PAC setting on with the engine stopped would make the two
        # sites unreachable, so closing the app turns protection off.
        try:
            if system.routing_enabled(self.pac_url):
                system.disable_routing(self.pac_url)
        finally:
            self.proxy.stop()
            self.root.destroy()


def run_app() -> int:
    setup_logging()
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
