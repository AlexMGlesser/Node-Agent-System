#!/usr/bin/env python3
"""
indexer.py  —  Command-line tool for adding content to the local vector database.

Usage:
  python indexer.py docs   <path>          # index a file or directory of text/markdown files
  python indexer.py code   <path>          # index a file or directory of source code
  python indexer.py web    <url>           # scrape and index a web page
  python indexer.py stats                  # show how many chunks are stored per collection
  python indexer.py delete <source>        # remove a source from the documents collection
  python indexer.py delete <source> --col  # remove from a specific collection

Examples:
  python indexer.py docs  ~/notes
  python indexer.py code  ~/projects/myapp/src
  python indexer.py web   https://docs.python.org/3/library/pathlib.html
  python indexer.py stats
  python indexer.py delete ~/notes/todo.md
"""

import argparse
import os
import sys
from pathlib import Path

import requests

import storage

# ── File extension mappings ───────────────────────────────────────────────────

DOCUMENT_EXTENSIONS = {".txt", ".md", ".rst", ".org", ".csv"}
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h",
    ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".bash", ".zsh",
    ".yaml", ".yml", ".toml", ".json", ".xml", ".html", ".css",
    ".sql", ".dockerfile", ".tf",
}
LANGUAGE_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".jsx": "javascript", ".tsx": "typescript", ".java": "java",
    ".c": "c", ".cpp": "cpp", ".h": "c", ".cs": "csharp",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".sh": "bash", ".bash": "bash", ".zsh": "zsh",
    ".sql": "sql", ".html": "html", ".css": "css",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".json": "json", ".xml": "xml",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_file(path: Path) -> str | None:
    """Read a file as UTF-8 text, returning None on binary/encoding errors."""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return None


def _collect_files(path: Path, extensions: set[str]) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in extensions else []
    files = []
    for root, _, filenames in os.walk(path):
        # Skip hidden directories and common junk folders
        rel = Path(root).relative_to(path)
        if any(part.startswith(".") or part in ("node_modules", "__pycache__", "venv", ".git") for part in rel.parts):
            continue
        for filename in filenames:
            fp = Path(root) / filename
            if fp.suffix.lower() in extensions:
                files.append(fp)
    return sorted(files)


def _scrape_url(url: str) -> tuple[str, str]:
    """
    Fetch a web page and return (title, plain_text).
    Uses BeautifulSoup when available, falls back to raw text stripping.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; node-agent-indexer/1.0)"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    raw_html = resp.text

    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(raw_html, "html.parser")
        title = soup.title.string.strip() if soup.title else url
        # Remove scripts/styles
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
    except ImportError:
        # BeautifulSoup not installed — strip HTML tags crudely
        import re
        title = url
        text = re.sub(r"<[^>]+>", " ", raw_html)
        text = re.sub(r"\s+", " ", text).strip()

    return title, text


# ── Sub-commands ──────────────────────────────────────────────────────────────

def cmd_docs(path_str: str) -> None:
    path = Path(path_str).expanduser().resolve()
    if not path.exists():
        print(f"[Error] Path not found: {path}")
        sys.exit(1)

    files = _collect_files(path, DOCUMENT_EXTENSIONS)
    if not files:
        print(f"[Warning] No document files found in: {path}")
        return

    total_chunks = 0
    for fp in files:
        text = _read_file(fp)
        if text is None:
            print(f"  [skip] {fp} (binary or unreadable)")
            continue
        if not text.strip():
            print(f"  [skip] {fp} (empty)")
            continue
        try:
            n = storage.add_document(text, source=str(fp))
            total_chunks += n
            print(f"  [ok] {fp}  ({n} chunk{'s' if n != 1 else ''})")
        except RuntimeError as exc:
            print(f"  [error] {fp}: {exc}")
            sys.exit(1)

    print(f"\nIndexed {len(files)} file(s) → {total_chunks} total chunks stored.")


def cmd_code(path_str: str) -> None:
    path = Path(path_str).expanduser().resolve()
    if not path.exists():
        print(f"[Error] Path not found: {path}")
        sys.exit(1)

    files = _collect_files(path, CODE_EXTENSIONS)
    if not files:
        print(f"[Warning] No code files found in: {path}")
        return

    total_chunks = 0
    for fp in files:
        text = _read_file(fp)
        if text is None:
            print(f"  [skip] {fp} (binary or unreadable)")
            continue
        if not text.strip():
            print(f"  [skip] {fp} (empty)")
            continue
        lang = LANGUAGE_MAP.get(fp.suffix.lower(), "")
        try:
            n = storage.add_code_file(text, filepath=str(fp), language=lang)
            total_chunks += n
            print(f"  [ok] {fp}  ({n} chunk{'s' if n != 1 else ''})")
        except RuntimeError as exc:
            print(f"  [error] {fp}: {exc}")
            sys.exit(1)

    print(f"\nIndexed {len(files)} file(s) → {total_chunks} total chunks stored.")


def cmd_web(url: str) -> None:
    print(f"Fetching: {url}")
    try:
        title, text = _scrape_url(url)
    except Exception as exc:
        print(f"[Error] Could not fetch page: {exc}")
        sys.exit(1)

    if not text.strip():
        print("[Error] Page returned no text content.")
        sys.exit(1)

    print(f"Title: {title}")
    print(f"Content length: {len(text)} chars")

    try:
        n = storage.add_web_page(text, url=url, title=title)
        print(f"[ok] Stored {n} chunk{'s' if n != 1 else ''}.")
    except RuntimeError as exc:
        print(f"[Error] {exc}")
        sys.exit(1)


def cmd_stats() -> None:
    counts = storage.stats()
    total = sum(counts.values())
    print("\nVector database contents:")
    print(f"  Documents  : {counts['documents']:>6} chunks")
    print(f"  Code       : {counts['code']:>6} chunks")
    print(f"  Web pages  : {counts['web']:>6} chunks")
    print(f"  Memory     : {counts['memory']:>6} chunks")
    print(f"  {'─'*22}")
    print(f"  Total      : {total:>6} chunks")
    print(f"\nDatabase location: {storage.CHROMA_DIR}")


def cmd_delete(source: str, collection: str) -> None:
    n = storage.delete_document(source, collection=collection)
    if n == 0:
        print(f"[Warning] No entries found for source: {source!r} in collection '{collection}'.")
    else:
        print(f"[ok] Deleted {n} chunk{'s' if n != 1 else ''} from '{collection}'.")


# ── Argument parsing & entry point ────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="indexer",
        description="Add content to the local vector database.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_docs = sub.add_parser("docs", help="Index a text/markdown file or directory")
    p_docs.add_argument("path", help="File or directory path")

    p_code = sub.add_parser("code", help="Index source code files")
    p_code.add_argument("path", help="File or directory path")

    p_web = sub.add_parser("web", help="Scrape and index a web page")
    p_web.add_argument("url", help="URL to fetch")

    sub.add_parser("stats", help="Show how many chunks are stored per collection")

    p_del = sub.add_parser("delete", help="Remove a source from the database")
    p_del.add_argument("source", help="The source string used when the content was indexed")
    p_del.add_argument(
        "--col",
        default=storage.COL_DOCUMENTS,
        choices=[storage.COL_DOCUMENTS, storage.COL_CODE, storage.COL_WEB, storage.COL_MEMORY],
        help="Which collection to delete from (default: documents)",
    )

    args = parser.parse_args()

    if args.command == "docs":
        cmd_docs(args.path)
    elif args.command == "code":
        cmd_code(args.path)
    elif args.command == "web":
        cmd_web(args.url)
    elif args.command == "stats":
        cmd_stats()
    elif args.command == "delete":
        cmd_delete(args.source, args.col)


if __name__ == "__main__":
    main()
