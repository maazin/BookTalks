# BookTalks

Drop in a PDF, get back a continuous audiobook with speed control, seeking,
jump-to-page, and a playback position that remembers itself. Built to run
locally at $0 — see [Deploying it publicly](#deploying-it-publicly) if you want
it reachable somewhere other than your own machine.

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

Narration is the only slow part, and it's network-bound: every call to
edge-tts opens a fresh connection and pays roughly a second of handshake
overhead before any audio comes back, regardless of how much text is in the
request. Two things follow from that.

**Pages are narrated several at a time**, not one after another — the
concurrency limit (`BOOKTALKS_TTS_CONCURRENCY`, 5 by default) bounds how many
of those network calls are in flight at once. Measured on a 30-page book
(12.7 minutes of audio):

| | Time |
|---|---|
| One page at a time | 61.5 s |
| Five at a time (default) | 10.4 s |

**Long pages are split into chunks** (edge-tts is more reliable under a
per-request size limit), and those chunks share the same concurrency pool as
everything else — a dense page needing 3 requests doesn't wait for them one
after another. On a page dense enough to need 2 chunks, rendering them
serially (the original approach) took **9–11 s**; concurrently, **1.5–2 s** —
because the fixed per-call overhead was being paid twice, in full, instead of
once. On a whole 10-page document at that density, end to end: **13 s**.

Assembly is stream-copy, not re-encode: five hours of audio joins in about four
seconds. Raise `BOOKTALKS_TTS_CONCURRENCY` for more speed, lower it if the TTS
service starts refusing requests — and expect real-world numbers to vary more
than these, since they depend on an external service's response time, which
fluctuates run to run.

**Per-page duration comes from edge-tts's own response, not a subprocess
call.** edge-tts already reports exactly when each sentence starts and ends —
it's how the library builds word-synced subtitles — so that same figure
answers "how long is this page's audio" without needing to ask `ffprobe`
separately. That's one fewer process spawned per page (down from one), which
barely registers on typical hardware but is real, non-free work on a
CPU-constrained deployment: Render's free tier, for instance, caps a service
at **0.1 CPU** — a tenth of a single core, not bursty, just always that small
— where spawning a process the extra ~450 times a 450-page book used to need
is a cost with nowhere to hide. Verified this doesn't cost accuracy: jump-to-
page offsets on a real 40-page narration landed within **15 ms** of the
actual audio boundaries, measured independently against the saved files —
tighter than the ffprobe-based measurement it replaced.

If narration is still visibly slowing down deep into a long document on a
constrained host, the next thing to check is that host's own CPU/memory
graphs during the run (Render's dashboard shows both) — that'll show directly
whether the instance itself is the ceiling, rather than guessing further from
the outside.

## Deploying it publicly

The app is still single-user with no accounts — putting it on a public URL
just means the "user" could now be anyone who finds the link. Two things
change when you do this:

1. **Set a password.** `BOOKTALKS_PASSWORD` turns on a login screen (a signed
   session cookie, not stored anywhere) gating every `/api/documents*` route.
   Unset — the default — there's no login at all, which is right for
   localhost and wrong for anything else. `/api/health` always stays open, for
   the platform's health checks.
2. **Decide if the free tier's storage is good enough.** A platform's free
   plan generally has no persistent disk — `/data` resets on every deploy, and
   sometimes on a plain restart. Fine for trying it out; not something to keep
   a real library on without paying for a disk.

### Render (recommended — one container, no CDN split needed)

`render.yaml` at the repo root is a Blueprint: it builds the root `Dockerfile`
(frontend built and served by the same FastAPI process, so there's one URL, no
CORS, and the session cookie is first-party) as a single free web service.

1. Push this repo to GitHub (already done if you're reading this from there).
2. In the Render dashboard: **New → Blueprint**, point it at the repo.
3. Render reads `render.yaml` and asks for one value it can't generate itself:
   `BOOKTALKS_PASSWORD`. Set it to whatever you want the login password to be.
   (`BOOKTALKS_SESSION_SECRET` is auto-generated; everything else in the
   blueprint has a sensible default.)
4. Deploy. First build takes a few minutes (it's compiling the frontend and
   installing ffmpeg); after that Render only rebuilds what changed.
5. Open the assigned `*.onrender.com` URL — you'll land on the password
   screen.

Free-plan specifics worth knowing before you rely on it:
- **Spins down after 15 minutes idle**, cold-starts (~30–60s) on the next
  request. A book mid-conversion when it spins down won't finish — reasonable
  for occasional use, annoying if you expect it always-on.
- **512 MB RAM** — `render.yaml` lowers `BOOKTALKS_TTS_CONCURRENCY` to 3 from
  the local default of 5 to leave headroom.
- **No persistent disk** — your library is wiped on every redeploy. Uncomment
  the `disk:` block in `render.yaml` and move to the Starter plan (~$7/mo
  instance + ~$1/GB/mo disk) once that's not acceptable.

### Vercel

Vercel only runs serverless functions — no persistent background jobs, no
writable disk between requests, both of which this backend needs (audio
generation happens after the upload request has already returned, and the
result has to still be there on the next request). It's a good fit for
*nothing* here as currently built. If you want Vercel specifically, the
backend still needs to live somewhere that can run a real container (Render,
Railway, Fly) — that reintroduces the cross-origin cookie problem the combined
image was built to avoid, and isn't the path this repo is set up for.

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
| `BOOKTALKS_CORS_ORIGINS` | localhost dev ports | Only matters for `vite dev` or a split frontend/backend deploy |
| `BOOKTALKS_STATIC_DIR` | `frontend/dist` | Built UI the API serves, when present |
| `BOOKTALKS_PASSWORD` | unset (no login) | Set to require a password — see [Deploying it publicly](#deploying-it-publicly) |
| `BOOKTALKS_SESSION_SECRET` | the password | Signs session cookies; set separately if sessions should survive a password change |
| `BOOKTALKS_SESSION_DAYS` | `30` | How long a login lasts |
| `BOOKTALKS_SECURE_COOKIES` | `false` | Mark the session cookie HTTPS-only — turn on for any public deployment |
| `BOOKTALKS_AUTH_RATE_LIMIT` | `10` | Failed logins allowed per IP before a 429 |
| `BOOKTALKS_AUTH_RATE_WINDOW_SEC` | `300` | Window the rate limit above resets over |

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

38 tests covering text cleanup, chunking, page-offset maths, upload validation,
range requests, playback state, deletion, voice selection, metadata-derived
durations, and the auth gate (login, logout, rate limiting,
forged/expired/replayed cookies). TTS is stubbed, so the suite
needs no network.

## Layout

```
backend/app/
  main.py       API endpoints, range streaming, static SPA hosting
  auth.py       Password gate: signed session cookies, rate limiting
  pipeline.py   Background job: extract → narrate → concatenate
  pdf_text.py   Extraction and cleanup
  tts.py        TTS interface + edge-tts engine
  audio.py      ffmpeg/ffprobe: durations, silence, stream-copy concatenation
  store.py      All SQL
  db.py         Schema and connections
frontend/src/
  App.jsx       Shell, routing, auth gate
  components/   Library, Player, Login, upload, dialogs, icons
  styles.css    Design system (tokens, type scale, components)
Dockerfile          Combined image (frontend + backend, one process) — hosting
backend/Dockerfile  API image — local docker-compose
frontend/Dockerfile nginx image — local docker-compose
render.yaml         Render Blueprint for the combined image
```

## Not in v1

Multi-user accounts (there's a password, not accounts — see
[Deploying it publicly](#deploying-it-publicly)), OCR for scanned PDFs, a voice
picker, word-level highlighting, and editing extracted text before narration.
