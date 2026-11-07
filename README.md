# Library app

A small self-hosted web app for reading image-based books, such as manga, comics and scans, from folders on your computer. Run it on one machine and read in any browser on your phone, tablet or laptop.

- One Python file, standard library only. Nothing to install with pip.
- Reading progress is saved on the server, so you can stop on one device and continue on another.
- Built for phones and tablets: a continuous vertical reader that hides its controls while you scroll.

## Requirements

- Python 3.10 or newer
- Linux with systemd, if you want it to start automatically (optional)

## Folder layout

Put each book in its own folder, with one subfolder per chapter and the page images inside:

```
~/Downloads/media/books/
└── Shingeki no Kyojin/
    ├── 0001 - Shingeki no Kyojin 0 Oneshot/
    │   ├── 001.jpg
    │   ├── 002.jpg
    │   └── ...
    ├── 0002 - Shingeki no Kyojin 1 To you in 2000 Years/
    └── ...
```

- **Book:** a folder whose subfolders are chapters. Books can also sit in subfolders, up to 4 levels below the library folder.
- **Chapter:** a folder with at least 2 images that make up at least 80% of its files. This keeps out folders such as movies that hold a single poster.
- **Images:** `.jpg`, `.jpeg`, `.png`, `.webp` and `.gif`.
- **Order:** chapters and pages are sorted by name, with numbers compared as numbers, so `2` comes before `10`.
- **Names:** a leading number such as `0001 - ` is hidden from chapter names in the app.
- **Folders starting with `.`** are ignored.

## Run it

```sh
git clone <this repo> ~/Downloads/work/library_app
cd ~/Downloads/work/library_app
python3 server.py
```

Then open http://localhost:8765.

The server listens on all network interfaces, so other devices on your network can open `http://<this machine's IP>:8765`. That also works over a VPN such as Tailscale.

New books show up within 30 seconds, after you reload the page.

### Options

| Option | Environment variable | Default |
|---|---|---|
| `--root` | `LIBRARY_ROOT` | `~/Downloads/media/books` |
| `--port` | `LIBRARY_PORT` | `8765` |
| `--host` | `LIBRARY_HOST` | `0.0.0.0` (all interfaces) |

```sh
python3 server.py --root ~/comics --port 9000
python3 server.py --host 127.0.0.1    # only this machine can open it
```

## Start it automatically

`library-app.service` is a systemd user service. It runs the server and restarts it if it crashes. It assumes the repo is in `~/Downloads/work/library_app`, so edit the paths in the file if yours is elsewhere. Library folder and port settings go on its commented `Environment=` lines.

```sh
cp library-app.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now library-app
```

A user service normally starts when you log in. To start it when the machine boots, before anyone logs in, turn on linger for your user:

```sh
loginctl enable-linger "$USER"
```

Everyday commands:

```sh
systemctl --user status library-app          # is it running?
systemctl --user restart library-app         # after changing server.py
journalctl --user -u library-app             # logs
systemctl --user disable --now library-app   # stop it and turn autostart off
```

Changes to `static/index.html` don't need a restart. Just reload the page.

## Using it

- **Home page:**
  - **Continue reading** lists the books you've started, most recent first. Tap one to go straight back to your page. The bar under each cover shows how far through the book you are.
  - **Library** lists every book, with search. Each cover is the first page of the book.
- **Book page:** the chapter list, a **Continue** button, and your current chapter highlighted.
- **Reader:** each chapter is one continuous vertical strip, and pages load as you scroll. The header and bottom bar hide while you scroll down and come back when you scroll up. The bottom bar has the previous and next chapter buttons, a chapter picker and the page number.

## Reading progress

Progress is stored in `progress.json` next to `server.py`, with one entry per book: the chapter and page you were last on, and when. The page is saved when it reaches the middle of the screen. The file isn't tracked by git.

- There's one saved place per book, shared by every device and everyone who uses the server.
- It's tied to the book's folder path. Renaming or moving a book folder loses its progress.

## Security

- The server only serves image files inside the library folder. Requests for paths outside it return 404.
- **There is no login.** Anyone who can reach the port can read your library and change reading progress. Run it only on a network you trust, or use `--host 127.0.0.1` together with a VPN or reverse proxy that handles access.

## Project files

| File | What it is |
|---|---|
| `server.py` | The HTTP server and JSON API |
| `static/index.html` | The whole web app: HTML, CSS and JavaScript in one page |
| `library-app.service` | systemd user service for autostart |
| `progress.json` | Saved reading progress, created on first use. Not in git |
| `LICENSE` | MIT license |

### API

| Endpoint | Returns |
|---|---|
| `GET /api/books` | Every book with its cover, chapter count and saved progress |
| `GET /api/book?id=<book>` | One book's chapters and page counts |
| `GET /api/chapter?book=<book>&ch=<index>` | The page image paths for one chapter |
| `GET /img?p=<path>` | One image, cached by the browser for a week |
| `POST /api/progress` | Saves `{"book", "chapter", "page"}`. Chapter and page count from 0 |

`<book>` is the book folder's path relative to the library folder.

## License

MIT. See [LICENSE](LICENSE).
