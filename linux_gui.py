#!/usr/bin/env python3
"""
linux_gui.py  —  Claude-style desktop UI for the Linux node.

Runs the same pipeline as linux_node.py:
  - prompt analysis and routing via small local model
  - retrieval context from local ChromaDB (storage.py)
  - local answer for simple requests / remote relay for complex requests
  - persistent conversation memory

Usage:
  .venv/bin/python linux_gui.py
"""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog
from pathlib import Path
import requests

import linux_node as core
import storage
from indexer import _scrape_url


class NodeAgentGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Node Agent Chat")
        self.root.geometry("1520x720")
        self.root.configure(bg="#1f1f1d")
        self.debug_visible = True

        self.worker_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.is_busy = False

        self._build_ui()
        self._refresh_stats()
        self.root.after(60, self._drain_queue)

    def _build_ui(self) -> None:
        # Sidebar
        sidebar = tk.Frame(self.root, bg="#191917", width=270)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)

        title = tk.Label(
            sidebar,
            text="Node Agent",
            fg="#f1f0ec",
            bg="#191917",
            font=("Helvetica", 18, "bold"),
            anchor="w",
            padx=14,
            pady=16,
        )
        title.pack(fill=tk.X)

        model_info = (
            f"Local:  {core.SMALL_MODEL}\n"
            f"Remote: {core.LARGE_MODEL}\n"
            f"Relay:  {core.WINDOWS_RELAY}"
        )
        self.info_label = tk.Label(
            sidebar,
            text=model_info,
            fg="#b8b6ad",
            bg="#191917",
            justify=tk.LEFT,
            anchor="w",
            padx=14,
            pady=8,
            font=("Helvetica", 10),
        )
        self.info_label.pack(fill=tk.X)

        self.stats_label = tk.Label(
            sidebar,
            text="DB: loading...",
            fg="#d6d4cc",
            bg="#191917",
            justify=tk.LEFT,
            anchor="w",
            padx=14,
            pady=10,
            font=("Helvetica", 10),
        )
        self.stats_label.pack(fill=tk.X)

        self.status_label = tk.Label(
            sidebar,
            text="Ready",
            fg="#89d185",
            bg="#191917",
            justify=tk.LEFT,
            anchor="w",
            padx=14,
            pady=8,
            font=("Helvetica", 10, "bold"),
        )
        self.status_label.pack(fill=tk.X)

        button_style = {
            "bg": "#2b2a28",
            "fg": "#f0efe8",
            "activebackground": "#3a3936",
            "activeforeground": "#ffffff",
            "relief": tk.FLAT,
            "font": ("Helvetica", 10),
            "anchor": "w",
            "padx": 12,
            "pady": 8,
            "bd": 0,
            "highlightthickness": 0,
        }

        tk.Button(sidebar, text="Index Docs Folder", command=self.index_docs, **button_style).pack(fill=tk.X, padx=12, pady=6)
        tk.Button(sidebar, text="Index Code Folder", command=self.index_code, **button_style).pack(fill=tk.X, padx=12, pady=6)
        tk.Button(sidebar, text="Index Web URL", command=self.index_web, **button_style).pack(fill=tk.X, padx=12, pady=6)
        tk.Button(sidebar, text="Refresh DB Stats", command=self._refresh_stats, **button_style).pack(fill=tk.X, padx=12, pady=6)
        tk.Button(sidebar, text="Clear Chat", command=self.clear_chat, **button_style).pack(fill=tk.X, padx=12, pady=6)
        tk.Button(sidebar, text="Toggle Debug", command=self.toggle_debug, **button_style).pack(fill=tk.X, padx=12, pady=6)

        # Main area
        main = tk.Frame(self.root, bg="#22211f")
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Debug panel (right side)
        self.debug_frame = tk.Frame(self.root, bg="#171715", width=360)
        self.debug_frame.pack(side=tk.RIGHT, fill=tk.Y)
        self.debug_frame.pack_propagate(False)

        tk.Label(
            self.debug_frame,
            text="Model Debug",
            fg="#c8a86b",
            bg="#171715",
            font=("Helvetica", 12, "bold"),
            anchor="w",
            padx=10,
            pady=10,
        ).pack(fill=tk.X)

        tk.Button(
            self.debug_frame,
            text="Clear",
            command=self.clear_debug,
            bg="#2b2a28",
            fg="#c0beb6",
            relief=tk.FLAT,
            bd=0,
            font=("Helvetica", 9),
            padx=8,
            pady=4,
        ).pack(anchor="e", padx=8)

        self.debug_log = scrolledtext.ScrolledText(
            self.debug_frame,
            wrap=tk.WORD,
            bg="#171715",
            fg="#b8cfa8",
            insertbackground="#b8cfa8",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=8,
            font=("Consolas", 9),
            state=tk.NORMAL,
        )
        self.debug_log.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 8))

        self.debug_log.tag_config("section", foreground="#c8a86b", font=("Consolas", 9, "bold"))
        self.debug_log.tag_config("label",   foreground="#8ec5ff", font=("Consolas", 9, "bold"))
        self.debug_log.tag_config("value",   foreground="#b8cfa8", font=("Consolas", 9))
        self.debug_log.tag_config("muted",   foreground="#666460", font=("Consolas", 9, "italic"))

        self.chat = scrolledtext.ScrolledText(
            main,
            wrap=tk.WORD,
            bg="#22211f",
            fg="#e8e6df",
            insertbackground="#f2f1eb",
            relief=tk.FLAT,
            bd=0,
            padx=22,
            pady=18,
            font=("Helvetica", 12),
            state=tk.NORMAL,
        )
        self.chat.pack(fill=tk.BOTH, expand=True, padx=14, pady=(14, 8))

        self.chat.tag_config("user_header", foreground="#e8d48d", font=("Helvetica", 11, "bold"))
        self.chat.tag_config("assistant_header", foreground="#8ec5ff", font=("Helvetica", 11, "bold"))
        self.chat.tag_config("system", foreground="#9aa59f", font=("Helvetica", 10, "italic"))
        self.chat.tag_config("body", foreground="#ece9df", font=("Helvetica", 12))

        input_row = tk.Frame(main, bg="#22211f")
        input_row.pack(fill=tk.X, padx=14, pady=(0, 14))

        self.prompt_box = tk.Text(
            input_row,
            height=4,
            bg="#2b2a28",
            fg="#f6f5ef",
            insertbackground="#f6f5ef",
            relief=tk.FLAT,
            bd=0,
            font=("Helvetica", 11),
            padx=12,
            pady=10,
        )
        self.prompt_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.prompt_box.bind("<Control-Return>", self._on_send_shortcut)

        self.send_btn = tk.Button(
            input_row,
            text="Send",
            command=self.send_prompt,
            bg="#c08a2a",
            fg="#1f1f1d",
            activebackground="#d49a32",
            activeforeground="#1b1a19",
            relief=tk.FLAT,
            bd=0,
            padx=20,
            pady=10,
            font=("Helvetica", 11, "bold"),
        )
        self.send_btn.pack(side=tk.LEFT, padx=(10, 0))

        self._add_system("Welcome. Ask anything and I will route local vs remote automatically.")
        self._debug_append("section", "Debug panel ready\n")
        self._debug_append("muted", f"Local:  {core.SMALL_MODEL}\nRemote: {core.LARGE_MODEL}\nRelay:  {core.WINDOWS_RELAY}\n")

    def toggle_debug(self) -> None:
        if self.debug_visible:
            self.debug_frame.pack_forget()
            self.debug_visible = False
        else:
            self.debug_frame.pack(side=tk.RIGHT, fill=tk.Y)
            self.debug_visible = True

    def clear_debug(self) -> None:
        self.debug_log.delete("1.0", tk.END)

    def _debug_append(self, tag: str, text: str) -> None:
        self.debug_log.insert(tk.END, text, tag)
        self.debug_log.see(tk.END)

    def _add_system(self, text: str) -> None:
        self.chat.insert(tk.END, f"\n{text}\n", "system")
        self.chat.see(tk.END)

    def _add_user(self, text: str) -> None:
        self.chat.insert(tk.END, "\nYou\n", "user_header")
        self.chat.insert(tk.END, f"{text}\n", "body")
        self.chat.see(tk.END)

    def _add_assistant_header(self) -> None:
        self.chat.insert(tk.END, "\nAssistant\n", "assistant_header")
        self.chat.see(tk.END)

    def _append_assistant_token(self, token: str) -> None:
        self.chat.insert(tk.END, token, "body")
        self.chat.see(tk.END)

    def _finish_assistant_message(self) -> None:
        self.chat.insert(tk.END, "\n", "body")
        self.chat.see(tk.END)

    def _set_busy(self, busy: bool) -> None:
        self.is_busy = busy
        self.send_btn.configure(state=tk.DISABLED if busy else tk.NORMAL)
        if busy:
            self.status_label.configure(text="Thinking...", fg="#e7b15a")
        else:
            self.status_label.configure(text="Ready", fg="#89d185")

    def _on_send_shortcut(self, _event: tk.Event) -> str:
        self.send_prompt()
        return "break"

    def send_prompt(self) -> None:
        if self.is_busy:
            return

        user_input = self.prompt_box.get("1.0", tk.END).strip()
        if not user_input:
            return

        self.prompt_box.delete("1.0", tk.END)
        self._add_user(user_input)
        self._set_busy(True)

        worker = threading.Thread(target=self._run_pipeline, args=(user_input,), daemon=True)
        worker.start()

    def _stream_local(self, prompt: str, system: str = ""):
        payload: dict = {"model": core.SMALL_MODEL, "prompt": prompt, "stream": True}
        if system:
            payload["system"] = system

        with requests.post(
            f"{core.LOCAL_OLLAMA}/api/generate",
            json=payload,
            stream=True,
            timeout=300,
        ) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines():
                if not raw:
                    continue
                data = json.loads(raw)
                token = data.get("response", "")
                if token:
                    yield token
                if data.get("done"):
                    break

    def _stream_remote(self, prompt: str):
        payload = {"model": core.LARGE_MODEL, "prompt": prompt, "system": core.SYSTEM_ASSISTANT}
        headers = {"Content-Type": "application/json"}
        if core.API_KEY:
            headers["X-API-Key"] = core.API_KEY

        with requests.post(
            f"{core.WINDOWS_RELAY}/generate",
            json=payload,
            headers=headers,
            stream=True,
            timeout=300,
        ) as resp:
            if resp.status_code == 401:
                raise RuntimeError("Relay rejected API key")
            resp.raise_for_status()
            for raw in resp.iter_lines():
                if not raw:
                    continue
                data = json.loads(raw)
                if "error" in data:
                    raise RuntimeError(str(data["error"]))
                token = data.get("response", "")
                if token:
                    yield token
                if data.get("done"):
                    break

    def _run_pipeline(self, user_input: str) -> None:
        try:
            # ── Step 1: small model rewrites the prompt ───────────────────────
            self.worker_queue.put(("debug", "section", "── Step 1: Prompt Analysis ──────────────\n"))
            self.worker_queue.put(("debug", "label",   "User → Small Model:\n"))
            self.worker_queue.put(("debug", "value",   user_input + "\n\n"))

            route, optimised_prompt = core.analyse_prompt(user_input)
            route_label = "LOCAL" if route == "local" else "REMOTE"

            self.worker_queue.put(("debug", "label",   f"Small Model → Route: "))
            self.worker_queue.put(("debug", "value",   f"{route_label}\n"))
            self.worker_queue.put(("debug", "label",   "Small Model → Rewritten prompt:\n"))
            self.worker_queue.put(("debug", "value",   optimised_prompt + "\n\n"))
            self.worker_queue.put(("system", f"Routing: {route_label}"))

            # ── Step 2: RAG retrieval ─────────────────────────────────────────
            self.worker_queue.put(("debug", "section", "── Step 2: Context Retrieval ────────────\n"))
            context = ""
            try:
                context = storage.build_context(user_input)
            except Exception as exc:
                self.worker_queue.put(("system", f"Storage warning: {exc}"))
                self.worker_queue.put(("debug", "muted", f"Storage error: {exc}\n"))

            if context:
                self.worker_queue.put(("system", f"Retrieved context: {len(context)} chars"))
                self.worker_queue.put(("debug", "label",  f"Retrieved {len(context)} chars from DB\n"))
                self.worker_queue.put(("debug", "muted",  context[:600] + ("..." if len(context) > 600 else "") + "\n\n"))
                final_prompt = (
                    "Use the following retrieved context to help answer the question.\n"
                    "If the context is not relevant, ignore it.\n\n"
                    f"{context}\n\n"
                    f"Question:\n{optimised_prompt}"
                )
            else:
                self.worker_queue.put(("debug", "muted", "No context retrieved\n\n"))
                final_prompt = optimised_prompt

            # ── Step 3: generation ────────────────────────────────────────────
            target = core.SMALL_MODEL if route == "local" else core.LARGE_MODEL
            dest   = "local Ollama" if route == "local" else f"Windows relay → {target}"
            self.worker_queue.put(("debug", "section", "── Step 3: Generation ───────────────────\n"))
            self.worker_queue.put(("debug", "label",   f"Sending to: {dest}\n"))
            self.worker_queue.put(("debug", "label",   "Final prompt sent:\n"))
            preview = final_prompt[:500] + ("..." if len(final_prompt) > 500 else "")
            self.worker_queue.put(("debug", "muted",   preview + "\n\n"))
            self.worker_queue.put(("debug", "label",   f"{target} → streaming response:\n"))

            self.worker_queue.put(("assistant_header", ""))
            answer_parts: list[str] = []

            system = core.SYSTEM_ASSISTANT
            for token in (self._stream_local(final_prompt, system=system) if route == "local" else self._stream_remote(final_prompt)):
                answer_parts.append(token)
                self.worker_queue.put(("token", token))

            full_answer = "".join(answer_parts).strip()
            if full_answer:
                try:
                    storage.save_memory(user_input, full_answer, session_id=core.SESSION_ID)
                except Exception:
                    pass

            self.worker_queue.put(("debug", "section", "\n── Done ─────────────────────────────────\n"))
            self.worker_queue.put(("done", ""))
            self.worker_queue.put(("refresh_stats", ""))

        except requests.exceptions.ConnectionError as exc:
            failed_url = ""
            req = getattr(exc, "request", None)
            if req is not None:
                failed_url = getattr(req, "url", "") or ""

            if "11434" in failed_url:
                msg = (
                    f"Could not reach local Ollama at {core.LOCAL_OLLAMA}. "
                    "Start Ollama with: ollama serve"
                )
            elif "4648" in failed_url or "/generate" in failed_url or "/health" in failed_url:
                msg = (
                    f"Could not reach Windows relay at {core.WINDOWS_RELAY}. "
                    "Confirm windows_relay.py is running and firewall allows port 4648."
                )
            else:
                msg = (
                    "Network error while contacting model service. "
                    f"Local: {core.LOCAL_OLLAMA} | Relay: {core.WINDOWS_RELAY}"
                )

            self.worker_queue.put(("error", msg))
            self.worker_queue.put(("debug", "section", "\n── Network Error ────────────────\n"))
            self.worker_queue.put(("debug", "value", msg + "\n"))
        except Exception as exc:
            self.worker_queue.put(("error", str(exc)))
            self.worker_queue.put(("debug", "section", "\n── Exception ────────────────────\n"))
            self.worker_queue.put(("debug", "value", str(exc) + "\n"))

    def _drain_queue(self) -> None:
        try:
            while True:
                item = self.worker_queue.get_nowait()
                event = item[0]
                if event == "debug":
                    _, tag, text = item
                    self._debug_append(tag, text)
                elif event == "assistant_header":
                    self._add_assistant_header()
                elif event == "token":
                    self._append_assistant_token(item[1])
                elif event == "system":
                    self._add_system(item[1])
                elif event == "error":
                    self._add_system(f"Error: {item[1]}")
                    self._debug_append("section", f"\n── Error ─────────────────────────────────\n")
                    self._debug_append("value", item[1] + "\n")
                    self._set_busy(False)
                elif event == "done":
                    self._finish_assistant_message()
                    self._set_busy(False)
                elif event == "refresh_stats":
                    self._refresh_stats()
        except queue.Empty:
            pass

        self.root.after(60, self._drain_queue)

    def _refresh_stats(self) -> None:
        try:
            s = storage.stats()
            total = sum(s.values())
            text = (
                f"DB chunks: {total}\n"
                f"docs: {s['documents']}\n"
                f"code: {s['code']}\n"
                f"web: {s['web']}\n"
                f"memory: {s['memory']}"
            )
            self.stats_label.configure(text=text)
        except Exception as exc:
            self.stats_label.configure(text=f"DB unavailable: {exc}")

    def _index_path(self, kind: str, path: Path) -> None:
        if not path.exists():
            messagebox.showerror("Error", f"Path not found: {path}")
            return

        self._add_system(f"Indexing {kind}: {path}")

        try:
            files = []
            if path.is_file():
                files = [path]
            else:
                files = [p for p in path.rglob("*") if p.is_file()]

            total_chunks = 0
            indexed_files = 0

            for fp in files:
                suffix = fp.suffix.lower()
                if kind == "docs" and suffix not in {".txt", ".md", ".rst", ".org", ".csv"}:
                    continue
                if kind == "code" and suffix not in {
                    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp",
                    ".h", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".sql",
                    ".json", ".yaml", ".yml", ".toml", ".html", ".css",
                }:
                    continue

                try:
                    text = fp.read_text(encoding="utf-8")
                except Exception:
                    continue
                if not text.strip():
                    continue

                if kind == "docs":
                    n = storage.add_document(text, source=str(fp))
                else:
                    n = storage.add_code_file(text, filepath=str(fp), language=suffix.lstrip("."))
                total_chunks += n
                indexed_files += 1

            self._add_system(f"Indexed {indexed_files} files and stored {total_chunks} chunks.")
            self._refresh_stats()
        except Exception as exc:
            messagebox.showerror("Indexing Error", str(exc))

    def index_docs(self) -> None:
        selected = filedialog.askdirectory(title="Choose docs folder")
        if selected:
            self._index_path("docs", Path(selected))

    def index_code(self) -> None:
        selected = filedialog.askdirectory(title="Choose code folder")
        if selected:
            self._index_path("code", Path(selected))

    def index_web(self) -> None:
        url = simpledialog.askstring("Index URL", "Enter URL to scrape:")
        if not url:
            return

        try:
            title, text = _scrape_url(url)
            chunks = storage.add_web_page(text, url=url, title=title)
            self._add_system(f"Indexed URL: {title} ({chunks} chunks)")
            self._refresh_stats()
        except Exception as exc:
            messagebox.showerror("Web Index Error", str(exc))

    def clear_chat(self) -> None:
        self.chat.delete("1.0", tk.END)
        self._add_system("Chat cleared.")


def main() -> None:
    root = tk.Tk()
    NodeAgentGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
