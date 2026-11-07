#!/usr/bin/env python3
"""Library app: serves image-based books from a folder over HTTP.

Layout it expects:
    <library root>/.../<Book>/<Chapter>/<page>.jpg|png

A folder counts as a book when at least one of its subfolders holds images.
Uses only the Python standard library.
"""

import argparse
import json
import mimetypes
import os
import re
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
PROGRESS_FILE = APP_DIR / "progress.json"
MAX_DEPTH = 4          # how deep to look for book folders under the root
SCAN_TTL = 30          # seconds to cache a library scan

ROOT: Path = Path()
_scan_cache = {"time": 0.0, "books": []}
_scan_lock = threading.Lock()
_progress_lock = threading.Lock()


def natural_key(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def is_image(p: os.DirEntry | Path) -> bool:
    return Path(p.name).suffix.lower() in IMAGE_EXTS


def visible_dirs(path: Path):
    try:
        with os.scandir(path) as it:
            return sorted((e for e in it if e.is_dir() and not e.name.startswith(".")),
                          key=lambda e: natural_key(e.name))
    except OSError:
        return []


def images_in(path: Path):
    try:
        with os.scandir(path) as it:
            return sorted((e.name for e in it if e.is_file() and is_image(e)), key=natural_key)
    except OSError:
        return []


def has_images(path: Path) -> bool:
    """True when the folder looks like a chapter: 2+ images making up most of its files.

    This keeps out folders like movies that hold one poster next to a video.
    """
    try:
        with os.scandir(path) as it:
            files = [e for e in it if e.is_file() and not e.name.startswith(".")]
    except OSError:
        return False
    images = sum(1 for e in files if is_image(e))
    return images >= 2 and images >= 0.8 * len(files)


def scan_library():
    books = []

    def walk(path: Path, depth: int):
        subdirs = visible_dirs(path)
        chapters = [d for d in subdirs if has_images(Path(d.path))]
        if chapters:
            rel = path.relative_to(ROOT).as_posix()
            first_pages = images_in(Path(chapters[0].path))
            books.append({
                "id": rel,
                "title": path.name,
                "chapters": len(chapters),
                "cover": f"{rel}/{chapters[0].name}/{first_pages[0]}" if first_pages else None,
                "mtime": path.stat().st_mtime,
            })
            return
        if depth < MAX_DEPTH:
            for d in subdirs:
                walk(Path(d.path), depth + 1)

    walk(ROOT, 0)
    books.sort(key=lambda b: natural_key(b["title"]))
    return books


def get_books():
    with _scan_lock:
        if time.time() - _scan_cache["time"] > SCAN_TTL:
            _scan_cache["books"] = scan_library()
            _scan_cache["time"] = time.time()
        return _scan_cache["books"]


def safe_path(rel: str) -> Path | None:
    """Resolve a library-relative path, refusing anything outside the root."""
    if not rel:
        return None
    p = (ROOT / rel).resolve()
    if p != ROOT and ROOT not in p.parents:
        return None
    return p


def load_progress():
    try:
        return json.loads(PROGRESS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_progress(book: str, chapter: int, page: int):
    with _progress_lock:
        data = load_progress()
        data[book] = {"chapter": chapter, "page": page, "time": int(time.time())}
        tmp = PROGRESS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(PROGRESS_FILE)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet

    def send_json(self, obj, status=HTTPStatus.OK):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}

        if url.path == "/api/books":
            progress = load_progress()
            books = [dict(b, progress=progress.get(b["id"])) for b in get_books()]
            return self.send_json(books)

        if url.path == "/api/book":
            book = safe_path(q.get("id", ""))
            if not book or not book.is_dir():
                return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            chapters = []
            for d in visible_dirs(book):
                pages = images_in(Path(d.path))
                if pages:
                    chapters.append({"name": d.name, "pages": len(pages)})
            return self.send_json({
                "id": q["id"],
                "title": book.name,
                "chapters": chapters,
                "progress": load_progress().get(q["id"]),
            })

        if url.path == "/api/chapter":
            book = safe_path(q.get("book", ""))
            if not book or not book.is_dir():
                return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            chapters = [d for d in visible_dirs(book) if has_images(Path(d.path))]
            try:
                idx = int(q.get("ch", "0"))
                ch = chapters[idx]
            except (ValueError, IndexError):
                return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            rel = f"{q['book']}/{ch.name}"
            return self.send_json({
                "book": q["book"],
                "title": book.name,
                "index": idx,
                "name": ch.name,
                "total": len(chapters),
                "pages": [f"{rel}/{p}" for p in images_in(Path(ch.path))],
            })

        if url.path == "/img":
            p = safe_path(q.get("p", ""))
            if not p or not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
                return self.send_error(HTTPStatus.NOT_FOUND)
            return self.send_file(p)

        if url.path in ("/", "/index.html"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/api/progress":
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 10_000)
            data = json.loads(self.rfile.read(length))
            book, chapter, page = str(data["book"]), int(data["chapter"]), int(data["page"])
        except (ValueError, KeyError, TypeError):
            return self.send_json({"error": "bad request"}, HTTPStatus.BAD_REQUEST)
        if not safe_path(book):
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        save_progress(book, chapter, page)
        return self.send_json({"ok": True})

    def send_file(self, p: Path):
        st = p.stat()
        etag = f'"{int(st.st_mtime)}-{st.st_size}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.end_headers()
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(p.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(st.st_size))
        self.send_header("Cache-Control", "public, max-age=604800")
        self.send_header("ETag", etag)
        self.end_headers()
        with open(p, "rb") as f:
            try:
                while chunk := f.read(256 * 1024):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


def main():
    global ROOT
    ap = argparse.ArgumentParser(description="Serve an image-based book library.")
    ap.add_argument("--root", default=os.environ.get("LIBRARY_ROOT", os.path.expanduser("~/Downloads/media/books")),
                    help="folder that holds the books")
    ap.add_argument("--host", default=os.environ.get("LIBRARY_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("LIBRARY_PORT", "8765")))
    args = ap.parse_args()

    ROOT = Path(args.root).resolve()
    if not ROOT.is_dir():
        raise SystemExit(f"Library root not found: {ROOT}")
    print(f"Library: {ROOT}  ({len(get_books())} books)", flush=True)
    print(f"Serving on http://{args.host}:{args.port}", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
