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
import struct
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
_size_cache: dict = {}
SIZE_CACHE_MAX = 50_000


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


def chapter_dirs(book: Path):
    """The book's chapter folders, in reading order. Every endpoint uses this list."""
    return [d for d in visible_dirs(book) if has_images(Path(d.path))]


def scan_library():
    books = []

    def walk(path: Path, depth: int):
        chapters = chapter_dirs(path)
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
            for d in visible_dirs(path):
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


def find_book(rel: str):
    """Return (path, chapters) for a book inside the root, or None if there is no such book."""
    book = safe_path(rel)
    if not book or not book.is_dir():
        return None
    chapters = chapter_dirs(book)
    if not chapters:
        return None
    return book, chapters


def parse_index(value) -> int | None:
    """A non-negative whole-number index, or None.

    Takes a JSON integer or a string of digits. Fractions, infinities,
    booleans and negative numbers are refused rather than rounded.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,9}", value):
        return int(value)
    return None


def _exif_orientation(seg: bytes) -> int | None:
    """The EXIF orientation tag from a JPEG APP1 segment, if there is one."""
    if seg[:6] != b"Exif\0\0":
        return None
    t = seg[6:]
    order = {b"II": "<", b"MM": ">"}.get(t[:2])
    if not order:
        return None
    try:
        ifd = struct.unpack(order + "I", t[4:8])[0]
        count = struct.unpack(order + "H", t[ifd:ifd + 2])[0]
        for i in range(count):
            entry = t[ifd + 2 + 12 * i:ifd + 14 + 12 * i]
            if struct.unpack(order + "H", entry[:2])[0] == 0x0112:
                return struct.unpack(order + "H", entry[8:10])[0]
    except struct.error:
        pass
    return None


def _read_size(f) -> tuple[int, int] | None:
    """Width and height from an image file's header, as the browser will show it."""
    head = f.read(32)
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        return struct.unpack(">II", head[16:24])
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", head[6:10])
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        kind = head[12:16]
        if kind == b"VP8 ":
            w, h = struct.unpack("<HH", head[26:30])
            return w & 0x3FFF, h & 0x3FFF
        if kind == b"VP8L" and head[20] == 0x2F:
            bits = int.from_bytes(head[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if kind == b"VP8X":
            return int.from_bytes(head[24:27], "little") + 1, int.from_bytes(head[27:30], "little") + 1
        return None
    if head[:2] != b"\xff\xd8":
        return None
    # JPEG: walk the segments to the frame header, noting the EXIF orientation on the way.
    f.seek(2)
    orientation = None
    for _ in range(1000):
        b = f.read(1)
        while b == b"\xff":
            b = f.read(1)
        if not b:
            return None
        marker = b[0]
        if marker == 0x01 or 0xD0 <= marker <= 0xD9:
            continue
        length = struct.unpack(">H", f.read(2))[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">xHH", f.read(5))
            return (h, w) if orientation in (5, 6, 7, 8) else (w, h)
        if marker == 0xDA:
            return None
        if marker == 0xE1 and orientation is None:
            orientation = _exif_orientation(f.read(length - 2))
        else:
            f.seek(length - 2, 1)
    return None


def image_size(path: Path) -> list[int] | None:
    """[width, height] of an image, or None when the header can't be read. Cached."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _size_cache:
        try:
            with open(path, "rb") as f:
                size = _read_size(f)
        except (OSError, struct.error, ValueError, IndexError):
            size = None
        if len(_size_cache) >= SIZE_CACHE_MAX:
            _size_cache.clear()
        _size_cache[key] = list(size) if size and all(size) else None
    return _size_cache[key]


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
            found = find_book(q.get("id", ""))
            if not found:
                return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            book, dirs = found
            chapters = [{"name": d.name, "pages": len(images_in(Path(d.path)))} for d in dirs]
            return self.send_json({
                "id": q["id"],
                "title": book.name,
                "chapters": chapters,
                "progress": load_progress().get(q["id"]),
            })

        if url.path == "/api/chapter":
            found = find_book(q.get("book", ""))
            idx = parse_index(q.get("ch", "0"))
            if not found or idx is None or idx >= len(found[1]):
                return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            book, chapters = found
            ch = chapters[idx]
            rel = f"{q['book']}/{ch.name}"
            pages = images_in(Path(ch.path))
            return self.send_json({
                "book": q["book"],
                "title": book.name,
                "index": idx,
                "name": ch.name,
                "total": len(chapters),
                "pages": [f"{rel}/{p}" for p in pages],
                "sizes": [image_size(Path(ch.path) / p) for p in pages],
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
            book, chapter, page = str(data["book"]), parse_index(data["chapter"]), parse_index(data["page"])
        except (ValueError, KeyError, TypeError):
            return self.send_json({"error": "bad request"}, HTTPStatus.BAD_REQUEST)
        if chapter is None or page is None:
            return self.send_json({"error": "bad request"}, HTTPStatus.BAD_REQUEST)
        found = find_book(book)
        if not found or chapter >= len(found[1]):
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        if page >= len(images_in(Path(found[1][chapter].path))):
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
