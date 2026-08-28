# BookTalks

Drop in a PDF, get back a continuous audiobook with speed control, seeking,
jump-to-page, and a playback position that remembers itself. Runs entirely on
your machine. No API keys, no cloud services, **$0 to run**.

<!-- Stack: FastAPI · SQLite · edge-tts · PyMuPDF · ffmpeg · React + Vite -->

## Quick start

**Requirements:** Python 3.11+, Node 20+, and `ffmpeg` on your PATH
(`brew install ffmpeg`).

### One command, everything running

```bash
./scripts/start.sh
```

Builds the frontend and serves the whole app — UI and API — from a single
process at **http://localhost:8000**.

### Docker

```bash
docker compose up --build
```

Then open **http://localhost:8080**. Audio and the database live in `./data`,
which is bind-mounted, so your library survives a rebuild. (On Linux the API
container writes as root; add `user: "${UID}:${GID}"` to the `api` service if
you'd rather those files be owned by you.)

### Development (hot reload)

```bash
./scripts/dev.sh
```

FastAPI with `--reload` on :8000, Vite with HMR on **http://localhost:5173**
(the dev server proxies `/api` to the backend).

## How it works

1. **Upload** — the PDF is validated, stored at `data/uploads/{id}.pdf`, and a
   background task starts. One document is processed at a time.
2. **Extract** — PyMuPDF pulls text page by page. Cleanup rejoins words split
   across line breaks (`exam-\nple` → `example`), drops running
   headers/footers/page numbers that repeat across the document, and reflows
   wrapped lines into paragraphs so the narration sounds like prose.
3. **Narrate** — pages go to `edge-tts` five at a time and land as
   `data/audio/{id}/page_{n}.mp3`. A page that fails is marked failed and
   skipped; one bad page never sinks the document.
4. **Assemble** — pages are concatenated into `data/audio/{id}/full.mp3` with
   ffmpeg in stream-copy mode — nothing is re-encoded, so a five-hour book is
   assembled in about four seconds — and each page's start offset is written
   back to the database.
5. **Play** — the player streams that file with HTTP range requests, so seeking
   is instant and nothing has to download up front.

A scanned PDF with no text layer fails fast with a clear message rather than
producing silence. There's no OCR in v1.

One document is converted at a time (a module-level lock is the whole queue),
so run the API as a **single worker** — extra workers would each get their own
lock and their own idea of what's in flight.

## Speed

Narration is the only slow part, and it's network-bound, so pages are sent five
at a time. Measured on a 30-page book (12.7 minutes of audio):

| | Time |
|---|---|
| One page at a time | 61.5 s |
| Five at a time (default) | 10.4 s |

Assembly is stream-copy, not re-encode: five hours of audio joins in about four
seconds. Raise `BOOKTALKS_TTS_CONCURRENCY` for more speed, lower it if the TTS
service starts refusing requests.

## Using it

- **Speed:** 1×, 1.25×, 1.5×, 2× — the browser's native `playbackRate`, so
  there's no pitch distortion.
- **Keyboard:** `space` play/pause, `←` / `→` skip 15 seconds.
- **Jump to a page:** search by page number or by a phrase from the page.
- **Resume:** position and speed save every 10 seconds, on pause, and when you
  close the tab. Reopen and you're where you left off.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/documents` | Multipart PDF upload; starts processing |
| `GET` | `/api/documents` | Library listing |
| `GET` | `/api/documents/{id}` | Detail and live conversion progress |
| `GET` | `/api/documents/{id}/audio` | The audiobook, with range support |
| `GET` | `/api/documents/{id}/pages` | Page timeline for jump-to-page |
| `GET` | `/api/documents/{id}/playback` | Saved position and speed |
| `PUT` | `/api/documents/{id}/playback` | Save position and speed |
| `DELETE` | `/api/documents/{id}` | Remove the document, its pages, and its audio |

Interactive docs at http://localhost:8000/docs while the backend is running.

## Configuration

Environment variables, all optional:

| Variable | Default | Notes |
|---|---|---|
| `BOOKTALKS_DATA_DIR` | `./data` | Uploads, audio, and the SQLite file |
| `BOOKTALKS_DB_PATH` | `{DATA_DIR}/booktalks.db` | |
| `BOOKTALKS_TTS_VOICE` | `en-US-AriaNeural` | Any edge-tts voice (`edge-tts --list-voices`) |
| `BOOKTALKS_TTS_RATE` | `+0%` | Baseline narration speed |
| `BOOKTALKS_TTS_CONCURRENCY` | `5` | Pages narrated at once; lower it if the TTS service rate limits |
| `BOOKTALKS_MAX_UPLOAD_MB` | `200` | Upload size ceiling |
| `BOOKTALKS_CORS_ORIGINS` | localhost dev ports | Only matters for `vite dev` |
| `BOOKTALKS_STATIC_DIR` | `frontend/dist` | Built UI the API serves, when present |

## Swapping the TTS engine

Every text-to-speech call goes through one interface in
`backend/app/tts.py`. To move off edge-tts — to a local Kokoro-82M, say —
implement a class with `async def synthesize(self, text, out_path) -> float`
(returning the duration in seconds) and return it from `get_engine()`. Nothing
else in the codebase changes.

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

19 tests covering text cleanup, chunking, page-offset maths, upload validation,
range requests, playback state, and deletion. TTS is stubbed, so the suite needs
no network.

## Layout

```
backend/app/
  main.py       API endpoints, range streaming, static SPA hosting
  pipeline.py   Background job: extract → narrate → concatenate
  pdf_text.py   Extraction and cleanup
  tts.py        TTS interface + edge-tts engine
  audio.py      ffmpeg/ffprobe: durations, silence, stream-copy concatenation
  store.py      All SQL
  db.py         Schema and connections
frontend/src/
  App.jsx       Shell and routing
  components/   Library, Player, upload, dialogs, icons
  styles.css    Design system (tokens, type scale, components)
```

## Not in v1

Multi-user accounts, cloud hosting, OCR for scanned PDFs, a voice picker,
word-level highlighting, and editing extracted text before narration.
