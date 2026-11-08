# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.10+ application using only the standard library, with no dependency installation or frontend build step.

- `server.py`: threaded HTTP server, library scanning, JSON API, image serving, image-size reading (PNG, GIF, WebP and JPEG headers, including JPEG EXIF rotation), and reading-progress persistence.
- `static/index.html`: complete browser app, including HTML, CSS, JavaScript, and hash-based routing.
- `library-app.service`: optional systemd user service; its paths assume this checkout location.
- `README.md`: setup, library folder layout, configuration, API reference, and the **Open issues** list of known bugs.
- `LICENSE`: MIT license.
- `.gitignore`: ignores `progress.json`, `progress.tmp`, and `__pycache__/`.
- `progress.json`: generated, Git-ignored user data. Keep book images outside the repository.

## Build, Test, and Development Commands

- `python3 server.py --root /path/to/books --host 127.0.0.1`: run locally at `http://127.0.0.1:8765`; the library directory must exist.
- `python3 server.py --help`: list host, port, and library-root options.
- `python3 -m py_compile server.py`: check Python syntax.
- `curl http://127.0.0.1:8765/api/books`: inspect discovered books while the server runs.

Reload the browser after editing `static/index.html`. Restart the server after Python changes. There is no build, formatter, or linter configuration.

## Coding Style & Naming Conventions

Follow existing formatting: four-space indentation in Python and two-space JavaScript function bodies and CSS rules. Use `snake_case` for Python functions and variables, `UPPER_CASE` for constants, and `camelCase` for JavaScript functions and variables. Keep frontend changes within the existing library, book, reader, and router sections. Preserve the standard-library-only design and escape dynamic text before inserting HTML.

## Behavior Invariants

These rules fix past bugs. Keep them when changing the related code.

- **One chapter rule.** Every chapter list, including the library scan, `/api/book`, and `/api/chapter`, comes from `chapter_dirs()`. A different rule in one place makes chapter indices disagree, opening the wrong chapter or returning 404. The README's chapter definition must match `has_images()`.
- **Validate input before use.** Look up books with `find_book()`, which requires an existing folder with chapters inside the root. Parse indices with `parse_index()`, which accepts only non-negative whole numbers and refuses fractions, infinities, booleans, and negative values. Check chapter and page indices against the real ranges before reading or saving. A malformed progress request returns 400. Unknown books, out-of-range indices, and malformed `ch` query values return 404.
- **No stale navigation.** `route()` gives each navigation a number. Every view takes `isCurrent` and checks it after each `await` and in any late callback, so an older response can't overwrite a newer page.
- **Reader cleanup.** Anything the reader adds (observers, listeners, the control bar, body classes) must be removed in its `cleanup` function.
- **Don't lose progress.** Saves are debounced. A pending save must be sent at once on leaving the reader, on `pagehide`, and when the page becomes hidden, using a `keepalive` request.
- **Stable resume.** `/api/chapter` returns each page's `[width, height]` in `sizes`, and the reader uses it to reserve page heights before images load. When restoring a saved page, wait for any page above it without a known size to load or fail, then scroll. Start progress tracking only after that, so a displaced position is never saved. Don't add a timeout that starts tracking before the layout is settled.

## Testing Guidelines

There is no automated test suite or coverage threshold. For behavior changes, use a disposable library containing `Book/Chapter/001.jpg` and `002.jpg` with valid images. Check discovery, natural ordering, chapter navigation, image loading, search, and saved progress after reload. Verify reader changes at phone and desktop widths. Test invalid paths and malformed API requests when changing handlers. If adding automated tests, prefer standard-library `unittest` with `test_*.py` files and temporary fixtures; isolate progress writes from real user data.

- **Keep test saves away from real data.** `progress.json` is always written next to `server.py`, and there is no flag to move it. Copy `server.py` and `static/` to a temporary folder and run the copy for any test that saves progress.
- **Use a fuller test library for reader changes.** Two small images can't show scroll or resume bugs. Include books with a dozen or more tall pages (for example 800×1200), several books, and edge cases: a folder with a single image (not a chapter), hidden files such as `._001.png`, a symlink pointing outside the library, an EXIF-rotated JPEG, and an unreadable image file.
- **Test with slow and out-of-order responses.** Many reader bugs only appear when requests are slow. Delay API and image responses, for example with DevTools network throttling or request interception, and check fast navigation between pages, resuming deep into a chapter, and leaving the reader right after scrolling.
- **Turn off scroll anchoring when testing layout shifts.** Chrome and Firefox adjust the scroll position when content above the viewport changes size, which hides layout-shift bugs that users of browsers without scroll anchoring, such as Safari, will see. Test with `* { overflow-anchor: none !important; }` injected, or in Safari.
- **Browser automation.** Headless Chromium driven over the DevTools protocol works with no extra installs; Node 22 and newer have a built-in `WebSocket`. Keep such scripts outside the repository unless they become a maintained test suite.
- **Prove a test can fail.** For a bug fix, run the same check against the old code (`git show HEAD:<file>`) and confirm it fails there before trusting the pass.

## Commit & Pull Request Guidelines

History contains one descriptive commit, beginning `Library app:`; no stricter convention is established. Write concise, descriptive subjects. Pull requests should explain the behavior change, list validation performed, link relevant issues, and include screenshots for visible UI changes. Update `README.md` when configuration or API behavior changes.

Known bugs are tracked in the **Open issues** list in `README.md`. Tick or remove an item when its fix lands, and add newly found bugs there instead of fixing unrelated ones in the same change.

## Security & Configuration

Configure with CLI flags or `LIBRARY_ROOT`, `LIBRARY_HOST`, and `LIBRARY_PORT`. The default host is `0.0.0.0`, and there is no authentication. Use loopback for local development.

- Resolved-path containment applies to all code that lists or opens files, not only file serving: library scanning, chapter and page discovery, and image-size reads as well as `/img`. Symlinks can point outside the root, so check the resolved path. Discovery doesn't do this yet; see the open issues.
- Keep the image-extension restriction when serving files.
- Filter hidden files and folders (names starting with `.`) the same way everywhere. Page lists don't do this yet; see the open issues.
