#!/usr/bin/env python3
"""
windows_gui.py  —  Quick control panel for the Windows relay machine.

Features:
  - Start/stop windows_relay.py
  - Display local LAN IPs and health URL
  - Check local Ollama and relay health
  - Live relay log output

Usage:
  python windows_gui.py
"""

from __future__ import annotations

import queue
import socket
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext
import requests

import windows_relay as relay


class RelayControlGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Windows Relay Control")
        self.root.geometry("980x680")
        self.root.configure(bg="#201f1d")

        self.proc: subprocess.Popen[str] | None = None
        self.queue: queue.Queue[str] = queue.Queue()

        self._build_ui()
        self.root.after(80, self._drain_log_queue)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        top = tk.Frame(self.root, bg="#201f1d")
        top.pack(fill=tk.X, padx=16, pady=14)

        title = tk.Label(
            top,
            text="Relay Server",
            fg="#f0efe9",
            bg="#201f1d",
            font=("Helvetica", 18, "bold"),
        )
        title.pack(anchor="w")

        ip_lines = "\n".join(f"- {ip}" for ip in self._get_local_ips())
        info = (
            f"Ollama: {relay.OLLAMA_URL}\n"
            f"Model: {relay.DEFAULT_MODEL}\n"
            f"Port: {relay.PORT}\n"
            f"Health URL: http://localhost:{relay.PORT}/health\n"
            f"LAN IPs:\n{ip_lines}"
        )

        self.info_label = tk.Label(
            top,
            text=info,
            fg="#ccc9bf",
            bg="#201f1d",
            justify=tk.LEFT,
            font=("Helvetica", 10),
            anchor="w",
        )
        self.info_label.pack(anchor="w", pady=(8, 10))

        controls = tk.Frame(self.root, bg="#201f1d")
        controls.pack(fill=tk.X, padx=16)

        btn = {
            "relief": tk.FLAT,
            "bd": 0,
            "font": ("Helvetica", 10, "bold"),
            "padx": 14,
            "pady": 8,
        }

        self.start_btn = tk.Button(
            controls,
            text="Start Relay",
            command=self.start_relay,
            bg="#7fb26a",
            fg="#1e1e1c",
            activebackground="#95c878",
            **btn,
        )
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.stop_btn = tk.Button(
            controls,
            text="Stop Relay",
            command=self.stop_relay,
            bg="#c26f62",
            fg="#1e1e1c",
            activebackground="#d88374",
            state=tk.DISABLED,
            **btn,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            controls,
            text="Check Ollama",
            command=self.check_ollama,
            bg="#5b8fc8",
            fg="#f4f5f7",
            activebackground="#6da4df",
            **btn,
        ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            controls,
            text="Check Relay",
            command=self.check_relay,
            bg="#8b79c8",
            fg="#f4f5f7",
            activebackground="#9c8adb",
            **btn,
        ).pack(side=tk.LEFT)

        self.status = tk.Label(
            self.root,
            text="Relay stopped",
            fg="#e19b93",
            bg="#201f1d",
            font=("Helvetica", 10, "bold"),
        )
        self.status.pack(anchor="w", padx=16, pady=(10, 6))

        self.log = scrolledtext.ScrolledText(
            self.root,
            wrap=tk.WORD,
            bg="#282724",
            fg="#ece9df",
            insertbackground="#ece9df",
            relief=tk.FLAT,
            bd=0,
            padx=14,
            pady=12,
            font=("Consolas", 10),
        )
        self.log.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        self._append_log("Ready. Click Start Relay.")

    def _get_local_ips(self) -> list[str]:
        ips = set()
        try:
            hostname = socket.gethostname()
            for item in socket.getaddrinfo(hostname, None):
                ip = item[4][0]
                if "." in ip and not ip.startswith("127."):
                    ips.add(ip)
        except Exception:
            pass
        if not ips:
            ips.add("(could not detect; run ipconfig)")
        return sorted(ips)

    def _append_log(self, line: str) -> None:
        self.log.insert(tk.END, f"{line}\n")
        self.log.see(tk.END)

    def start_relay(self) -> None:
        if self.proc and self.proc.poll() is None:
            return

        cmd = [sys.executable, "windows_relay.py"]
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            messagebox.showerror("Start Error", str(exc))
            return

        threading.Thread(target=self._read_process_output, daemon=True).start()

        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.status.configure(text="Relay running", fg="#98d08a")
        self._append_log(f"Started relay with command: {' '.join(cmd)}")

    def _read_process_output(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        for line in self.proc.stdout:
            self.queue.put(line.rstrip())
        code = self.proc.wait()
        self.queue.put(f"[process exited with code {code}]")
        self.queue.put("__PROCESS_STOPPED__")

    def stop_relay(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self._append_log("Stopping relay...")

    def _drain_log_queue(self) -> None:
        try:
            while True:
                line = self.queue.get_nowait()
                if line == "__PROCESS_STOPPED__":
                    self.start_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)
                    self.status.configure(text="Relay stopped", fg="#e19b93")
                else:
                    self._append_log(line)
        except queue.Empty:
            pass

        self.root.after(80, self._drain_log_queue)

    def check_ollama(self) -> None:
        try:
            r = requests.get(f"{relay.OLLAMA_URL}/api/tags", timeout=5)
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
            self._append_log(f"Ollama OK. Models: {', '.join(models) if models else '(none)'}")
        except Exception as exc:
            self._append_log(f"Ollama check failed: {exc}")

    def check_relay(self) -> None:
        try:
            r = requests.get(f"http://localhost:{relay.PORT}/health", timeout=5)
            r.raise_for_status()
            data = r.json()
            self._append_log(f"Relay OK: {data}")
        except Exception as exc:
            self._append_log(f"Relay check failed: {exc}")

    def on_close(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    RelayControlGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
