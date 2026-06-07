"""
windows_relay.py  —  Runs on the Windows/4080 machine.

This is a lightweight HTTP relay server that sits in front of the local Ollama
instance and makes it accessible to the Linux node over the local network.

Responsibilities:
  1. Accept /generate requests from the Linux node.
  2. Forward them to the local Ollama API.
  3. Stream the response back to the caller in real time.
  4. Expose a /health endpoint so the Linux node can check connectivity.

Requirements:
  pip install flask requests
  Ollama must be running locally:  ollama serve
  Large model pulled:              ollama pull qwen3:30b

Usage:
  python windows_relay.py

Then on the Linux machine set WINDOWS_RELAY = "http://<this-PC's-IP>:4648"

Optional: set API_KEY below to require a shared secret.
         Set the same key in linux_node.py to enable authentication.
"""

import json
import sys
from flask import Flask, Response, jsonify, request
import requests

# ── Configuration ─────────────────────────────────────────────────────────────

OLLAMA_URL    = "http://localhost:11434"  # local Ollama instance
DEFAULT_MODEL = "qwen3:30b"              # large model to use when none specified
PORT          = 4648                     # port this server listens on
API_KEY       = None                     # set to a string to enable auth, e.g. "mysecretkey"

# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)


def _authorised() -> bool:
    """Return True if the request carries a valid API key (or no key is required)."""
    if API_KEY is None:
        return True
    return request.headers.get("X-API-Key") == API_KEY


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    """
    Returns the server status and the list of models available in Ollama.
    Useful for verifying network connectivity from the Linux node:
        curl http://<windows-ip>:4648/health
    """
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        return jsonify({"status": "ok", "models": models})
    except requests.exceptions.ConnectionError:
        return jsonify({"status": "error", "message": "Cannot reach local Ollama. Is 'ollama serve' running?"}), 503
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 503


@app.route("/generate", methods=["POST"])
def generate():
    """
    Accepts a JSON body:
        {
            "prompt":  "<text>",          # required
            "model":   "<model-name>",    # optional, falls back to DEFAULT_MODEL
            "system":  "<system-prompt>"  # optional
        }

    Streams back newline-delimited JSON exactly as Ollama returns it, so the
    Linux node can parse tokens as they arrive.
    """
    if not _authorised():
        return jsonify({"error": "Unauthorised — invalid or missing API key"}), 401

    body = request.get_json(silent=True)
    if not body or "prompt" not in body:
        return jsonify({"error": "Request body must be JSON with a 'prompt' field"}), 400

    prompt = body["prompt"]
    model  = body.get("model", DEFAULT_MODEL)
    system = body.get("system", "")

    ollama_payload: dict = {
        "model":  model,
        "prompt": prompt,
        "stream": True,
    }
    if system:
        ollama_payload["system"] = system

    def stream_from_ollama():
        try:
            with requests.post(
                f"{OLLAMA_URL}/api/generate",
                json=ollama_payload,
                stream=True,
                timeout=300,
            ) as ollama_resp:
                ollama_resp.raise_for_status()
                for line in ollama_resp.iter_lines():
                    if line:
                        yield line + b"\n"
        except requests.exceptions.ConnectionError:
            error = json.dumps({
                "error": "Cannot reach local Ollama. Is 'ollama serve' running?",
                "done": True,
            })
            yield error.encode() + b"\n"
        except Exception as exc:
            error = json.dumps({"error": str(exc), "done": True})
            yield error.encode() + b"\n"

    return Response(stream_from_ollama(), content_type="application/x-ndjson")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  Node Agent  —  Windows Relay Server")
    print("=" * 60)
    print(f"  Ollama URL    : {OLLAMA_URL}")
    print(f"  Default model : {DEFAULT_MODEL}")
    print(f"  Listening on  : 0.0.0.0:{PORT}")
    auth_status = f"enabled (key: {'*' * len(API_KEY)})" if API_KEY else "disabled"
    print(f"  API key auth  : {auth_status}")
    print("=" * 60)
    print(f"\n  Health check  : http://localhost:{PORT}/health")
    print(f"  From Linux    : http://<this-PC-IP>:{PORT}/health")
    print("\n  Press Ctrl-C to stop.\n")

    # Use threaded=True so multiple streaming requests don't block each other
    app.run(host="0.0.0.0", port=PORT, threaded=True)
