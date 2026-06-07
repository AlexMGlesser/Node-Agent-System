#!/usr/bin/env python3
"""
linux_node.py  —  Runs on the Linux/2060 machine.

Responsibilities:
  1. Accept user input from the terminal.
  2. Use a small local model to classify the request (simple vs. complex)
     and rewrite/optimise the prompt.
  3. Route simple questions back to the small model for a fast local answer.
  4. Route complex questions to the Windows relay server (4080) for a
     full-quality answer from the large model.
  5. Stream the response back to the terminal in real time.

Requirements:
  pip install requests
  Ollama must be running locally:  ollama serve
  Small model pulled:              ollama pull qwen3:4b

Configuration – change the four constants below before running.
"""

import json
import sys
import requests
import storage

# ── Configuration ────────────────────────────────────────────────────────────

SMALL_MODEL   = "qwen3:4b"               # model running locally on this PC
LARGE_MODEL   = "qwen3:30b"              # model running on the Windows PC
LOCAL_OLLAMA  = "http://localhost:11434" # local Ollama API endpoint
WINDOWS_RELAY = "http://192.168.1.50:5000"  # Windows PC IP + relay port
API_KEY       = None                     # must match windows_relay.py if set

# ── Prompt used to classify + rewrite user requests ──────────────────────────

SESSION_ID = "default"   # change this per session if you want isolated memory

SYSTEM_REWRITER = """\
You are a prompt analysis assistant integrated into a two-machine AI pipeline.

Classify the user's request and optimise the prompt for whichever model will
answer it.  Respond with ONLY valid JSON — no explanation, no markdown.

Rules:
  - route "local"  → question is simple, factual, or short-answer
                     (the small model on this machine will answer)
  - route "remote" → question requires deep reasoning, multi-step logic,
                     long-form writing, coding, or research
                     (the large model on the Windows machine will answer)

When rewriting the prompt:
  - Make it precise and unambiguous.
  - Add relevant domain context.
  - Specify the desired output format if appropriate.
  - Keep the user's original intent intact.

Output format (strictly):
{
  "route": "local" | "remote",
  "prompt": "<rewritten prompt>"
}
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    return headers


def stream_local(prompt: str, system: str = "") -> None:
    """Stream a response from the small local model and print it."""
    payload: dict = {
        "model": SMALL_MODEL,
        "prompt": prompt,
        "stream": True,
    }
    if system:
        payload["system"] = system

    try:
        with requests.post(
            f"{LOCAL_OLLAMA}/api/generate",
            json=payload,
            stream=True,
            timeout=120,
        ) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines():
                if not raw:
                    continue
                data = json.loads(raw)
                token = data.get("response", "")
                if token:
                    print(token, end="", flush=True)
                if data.get("done"):
                    break
    except requests.exceptions.ConnectionError:
        print("[Error] Cannot reach local Ollama. Is 'ollama serve' running?")
    except Exception as exc:
        print(f"[Error] Local model: {exc}")


def call_local_blocking(prompt: str, system: str = "") -> str:
    """Return a complete response from the small model (used for analysis)."""
    payload: dict = {
        "model": SMALL_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    if system:
        payload["system"] = system

    try:
        resp = requests.post(
            f"{LOCAL_OLLAMA}/api/generate",
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
    except requests.exceptions.ConnectionError:
        print("[Error] Cannot reach local Ollama. Is 'ollama serve' running?")
        return ""
    except Exception as exc:
        print(f"[Error] Local model: {exc}")
        return ""


def stream_remote(prompt: str) -> None:
    """Stream a response from the large model on the Windows relay server."""
    payload = {
        "model": LARGE_MODEL,
        "prompt": prompt,
    }
    try:
        with requests.post(
            f"{WINDOWS_RELAY}/generate",
            json=payload,
            headers=_build_headers(),
            stream=True,
            timeout=300,
        ) as resp:
            if resp.status_code == 401:
                print("[Error] Relay server rejected the API key.")
                return
            resp.raise_for_status()
            for raw in resp.iter_lines():
                if not raw:
                    continue
                data = json.loads(raw)
                if "error" in data:
                    print(f"[Error from relay] {data['error']}")
                    return
                token = data.get("response", "")
                if token:
                    print(token, end="", flush=True)
                if data.get("done"):
                    break
    except requests.exceptions.ConnectionError:
        print(
            f"[Error] Cannot reach Windows relay at {WINDOWS_RELAY}.\n"
            "Check that windows_relay.py is running and the IP is correct."
        )
    except Exception as exc:
        print(f"[Error] Remote model: {exc}")


# ── Core pipeline ─────────────────────────────────────────────────────────────

def analyse_prompt(user_prompt: str) -> tuple[str, str]:
    """
    Ask the small model to classify and rewrite the user's prompt.

    Returns (route, optimised_prompt) where route is "local" or "remote".
    Falls back to ("remote", original_prompt) if parsing fails.
    """
    analysis_request = f"User request:\n{user_prompt}"
    raw = call_local_blocking(analysis_request, system=SYSTEM_REWRITER)

    if not raw:
        return "remote", user_prompt

    # Extract the first JSON object from the response
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            data = json.loads(raw[start:end])
            route = data.get("route", "remote").strip().lower()
            optimised = data.get("prompt", user_prompt).strip()
            if route not in ("local", "remote"):
                route = "remote"
            return route, optimised
    except (json.JSONDecodeError, KeyError):
        pass

    return "remote", user_prompt


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  Node Agent  —  Linux Client")
    print("=" * 60)
    print(f"  Local  model : {SMALL_MODEL}  (this machine)")
    print(f"  Remote model : {LARGE_MODEL}  ({WINDOWS_RELAY})")
    print(f"  Session ID   : {SESSION_ID}")
    try:
        db = storage.stats()
        total = sum(db.values())
        print(f"  Vector DB    : {total} chunks  "
              f"(docs={db['documents']} code={db['code']} "
              f"web={db['web']} memory={db['memory']})")
    except Exception:
        print("  Vector DB    : (unavailable — run 'ollama serve' first)")
    print("  Type 'exit' or Ctrl-C to quit.")
    print("=" * 60)

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            sys.exit(0)

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            print("Goodbye.")
            sys.exit(0)

        # Step 1 – classify and rewrite
        print("\n[Analysing prompt…]", end="\r", flush=True)
        route, optimised_prompt = analyse_prompt(user_input)

        label = "LOCAL (small model)" if route == "local" else "REMOTE (large model)"
        print(f"[Routing to: {label}]          ")

        if optimised_prompt != user_input:
            preview = optimised_prompt[:100]
            if len(optimised_prompt) > 100:
                preview += "…"
            print(f"[Optimised: {preview}]")

        # Step 2 – retrieve relevant context from local database
        context = ""
        try:
            context = storage.build_context(user_input)
            if context:
                print(f"[Context retrieved: {len(context)} chars from local DB]")
        except RuntimeError as exc:
            print(f"[Storage warning: {exc}]")

        print()

        # Step 3 – assemble the final prompt (inject context when available)
        if context:
            final_prompt = (
                f"Use the following retrieved context to help answer the question.\n"
                f"If the context is not relevant, ignore it.\n\n"
                f"{context}\n\n"
                f"Question:\n{optimised_prompt}"
            )
        else:
            final_prompt = optimised_prompt

        # Step 4 – generate answer
        answer_parts: list[str] = []

        class _Capture:
            """Thin wrapper to capture streamed text for memory storage."""
            def __init__(self) -> None:
                self.buf: list[str] = []

        _cap = _Capture()
        _orig_write = sys.stdout.write

        def _capturing_write(s: str) -> int:
            _cap.buf.append(s)
            return _orig_write(s)

        sys.stdout.write = _capturing_write  # type: ignore[method-assign]
        try:
            if route == "local":
                stream_local(final_prompt)
            else:
                stream_remote(final_prompt)
        finally:
            sys.stdout.write = _orig_write  # type: ignore[method-assign]

        full_answer = "".join(_cap.buf).strip()

        # Step 5 – save turn to memory
        if full_answer:
            try:
                storage.save_memory(user_input, full_answer, session_id=SESSION_ID)
            except RuntimeError:
                pass  # memory saving is best-effort

        print()  # newline after streamed output


if __name__ == "__main__":
    main()
