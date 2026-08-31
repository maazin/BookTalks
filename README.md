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
3. **Narrate** — pages go to `edge-tts` sixteen requests at a time and land as
   `data/audio/{id}/page_{n}.mp3`. A page that fails is marked failed and
   skipped; one bad page never sinks the document.
4. **Assemble** — pages are concatenated into `data/audio/{id}/full.mp3` with
   ffmpeg in stream-copy mode — nothing is re-encoded, so thirteen hours of
   audio is assembled in about ten seconds — each page's start offset is
   written back to the database, and the per-page files are then deleted.
5. **Play** — the player streams that file with HTTP range requests, so seeking
   is instant and nothing has to download up front.

A scanned PDF with no text layer fails fast with a clear message rather than
producing silence. There's no OCR in v1.

One document is converted at a time (a module-level lock is the whole queue),
so run the API as a **single worker** — extra workers would each get their own
lock and their own idea of what's in flight.

## Speed

Narration is network-bound: every call to edge-tts opens a fresh connection
and pays roughly a second of handshake overhead before any audio comes back,
regardless of how much text is in the request. Almost all of the time is spent
waiting, not computing, so the throughput lever is **how many requests are in
flight at once** (`BOOKTALKS_TTS_CONCURRENCY`).

Measured end to end on a real 345-page book (13 hours of audio):

| | Time |
|---|---|
| Concurrency 5 (the old default) | 546 s |
| **Concurrency 16 (current default)** | **83 s** |

That's the whole book — extraction, 585 narration requests, and assembly —
with zero failed pages. Isolated sweeps on the same book, in pages/sec:

| concurrency | 5 | 10 | 16 | 24 | 32 |
|---|---|---|---|---|---|
| pages/sec | 1.7 | 2.8 | 4.7–5.4 | 6.1 | 9.3 |

No failures at any level tested. Past ~32 the numbers get noisy without
reliably improving, so 16 is the default: a large speedup that stays well
inside the range that never errored and leaves headroom on small instances,
where each connection costs memory and TLS handshake CPU. Raise it if your
host can take it.

**Long pages are split into chunks** (edge-tts is more reliable under a
per-request size limit), and chunks share that same concurrency pool — a
dense page needing 3 requests doesn't run them one after another.

**Assembly is stream-copy, not re-encode**: 13 hours of audio joins in about
ten seconds, and nothing is decoded or re-encoded. Per-page duration comes
from edge-tts's own response rather than a separate `ffprobe` subprocess, so
most pages spawn no processes at all during narration.

**Disk**: the per-page mp3s are deleted once they've been folded into the
finished audiobook — nothing ever serves them, and keeping them meant every
book occupied twice the disk it needed to. That 345-page book uses **273 MB**
instead of 543 MB.

## Sharing it with someone else

If one other person wants to use this from their own phone or laptop, you
don't need a host at all — run it here and open a tunnel to it:

```bash
BOOKTALKS_PASSWORD='something-only-you-two-know' ./scripts/share.sh
```

That builds the app, serves it on `127.0.0.1` (never on your local network),
and opens a Cloudflare Tunnel to it. It prints a public HTTPS link to share.
It refuses to start without a password, because that link is reachable by
anyone who has it.

Requires `cloudflared` (`brew install cloudflared`). No account, no card, no
port forwarding — the tunnel dials out, so nothing on your router changes.

**Tell her to add it to her home screen** (Share → Add to Home Screen on iOS,
"Install app" on Android). It then opens like a real app, and the lock-screen
play/pause/skip controls work — which is most of what listening to a long
audiobook on a phone involves.

Two caveats worth knowing:

- **It only works while that command is running**, so your machine has to be
  awake. Close the terminal or shut the lid and the link goes dead.
- **The link changes every time you run it.** Fine occasionally; annoying for
  someone non-technical. For a permanent address you need a Cloudflare account
  and a domain, then a *named* tunnel
  ([docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/)) —
  same script otherwise, and the URL never changes again.

If you'd rather it be reachable when your machine is off, that's when a real
host earns its keep — see below.

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
- **512 MB RAM and 0.1 CPU** — a tenth of a core, constantly. `render.yaml`
  lowers `BOOKTALKS_TTS_CONCURRENCY` to 8 from the local default of 16 to
  leave headroom. Expect conversions to take substantially longer here than
  the timings above, which were measured on unthrottled hardware.
- **No persistent disk** — this is the one that bites. Your entire library,
  database included, is wiped on every redeploy and on restarts. A long book
  you waited for can simply be gone next time you open the app, which shows up
  as the player saying the audiobook no longer exists. If you want a library
  that survives, uncomment the `disk:` block in `render.yaml` and move to the
  Starter plan (~$7/mo instance + ~$1/GB/mo disk). A 345-page book needs about
  273 MB, so 1 GB covers a few of them.

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
