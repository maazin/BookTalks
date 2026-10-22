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
   skipped; one bad page never sinks the document. If edge-tts starts failing
   for reasons that clearly aren't about the page, narration switches to a
   [local engine](#the-offline-fallback) for the rest of the book.
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

Everything below is about the default engine. The
[offline fallback](#the-offline-fallback) is CPU-bound instead, and has its own
numbers and its own (much lower) concurrency setting.

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
opens a tunnel, and prints a public HTTPS link to share. It refuses to start
without a password, because that link is reachable by anyone who has it.

**Tell her to add it to her home screen** (Share → Add to Home Screen on iOS,
"Install app" on Android). It then opens like a real app, and the lock-screen
play/pause/skip controls work — which is most of what listening to a long
audiobook on a phone involves.

The script picks the best tunnel available.

### A permanent link (Tailscale Funnel)

Free, and needs no domain. The address never changes, so you send it once.

```bash
brew install tailscale                      # no sudo needed

# Start the daemon in userspace mode (also no sudo):
mkdir -p ~/.booktalks-tailscale
tailscaled --tun=userspace-networking \
  --statedir=$HOME/.booktalks-tailscale \
  --socket=$HOME/.booktalks-tailscale/tailscaled.sock &

# Log in — this prints a URL to open in your browser:
tailscale --socket=$HOME/.booktalks-tailscale/tailscaled.sock up --hostname=booktalks
```

The first `./scripts/share.sh` after that will use the Funnel automatically and
print a permanent `https://booktalks.<your-tailnet>.ts.net` address. If
Tailscale reports that Funnel or HTTPS needs enabling, it prints the exact
admin-console link to click; that's a one-time step.

`--statedir` (a directory) rather than `--state` (a file) matters: Tailscale
stores the Funnel's TLS certificate under that var root, and without it
certificate provisioning fails with `no TailscaleVarRoot` and the URL serves
nothing over HTTPS.

Two one-time approvals happen in the Tailscale admin console the first time —
enabling Funnel for the machine, and HTTPS certificates for the tailnet. The
CLI prints the exact link to click when either is missing.

Worth knowing:

- Funnel traffic goes through Tailscale's network and is subject to a
  bandwidth cap you can't configure. For this app that's usually fine — the
  audio is 48 kbps, so an hour of listening is roughly 21 MB.
- **Some networks don't resolve `.ts.net`.** Corporate, university, and
  filtered DNS often return NXDOMAIN for it, so the link can look dead from
  *your* machine while working perfectly for everyone else. Check with
  `dig +short @1.1.1.1 <your-host>.ts.net` — if public DNS resolves it, the
  tunnel is fine and it's your local resolver. Pointing that machine at
  1.1.1.1 or 8.8.8.8 fixes it locally.

### No setup at all (temporary link)

With `cloudflared` installed (`brew install cloudflared`) and no Tailscale, the
same script falls back to a Cloudflare quick tunnel. Nothing to configure, but
**the URL changes every run**, which gets old fast for someone non-technical.

### Keeping it running

`./scripts/share.sh` stops when you close the terminal. To keep it up, save
the password once and run it under launchd:

```bash
mkdir -p ~/.booktalks && chmod 700 ~/.booktalks
printf "BOOKTALKS_PASSWORD='your-password'\n" > ~/.booktalks/env
chmod 600 ~/.booktalks/env
launchctl load ~/Library/LaunchAgents/com.booktalks.share.plist
```

The script reads that file when no password is passed, so the secret stays out
of the plist (LaunchAgent plists are world-readable) and out of your shell
history. `KeepAlive` restarts it if it crashes, and `RunAtLoad` brings it back
at login. Logs land in `~/.booktalks/logs/`.

**Put `tailscaled` under launchd too** (`com.booktalks.tailscaled.plist`, same
shape, `KeepAlive` on). A daemon started by hand with `nohup` survives a
closed terminal but not the session that started it being torn down — and
when it dies, Tailscale drops the Funnel's DNS record, `share.sh` silently
falls back to a temporary Cloudflare link, and the permanent address just
stops answering with nothing in the app saying why. That happened here.
`share.sh` now also waits up to 30s for the Tailscale socket at start so it
doesn't race the daemon into the fallback on every boot.

Stop it with `launchctl unload ~/Library/LaunchAgents/com.booktalks.share.plist`.

### If it says "can't connect"

Almost always one of three things, in order of likelihood:

1. **Nothing is running.** The link only answers while the service is up.
   `launchctl list | grep booktalks`, and `curl -s localhost:8000/api/auth/status`.
2. **DNS doesn't resolve `.ts.net` on that network.** This is the fragile part
   of Funnel. Filtered/corporate/university resolvers often NXDOMAIN it, and
   even public resolvers disagree — during setup here, `8.8.8.8`, `9.9.9.9`
   and OpenDNS all answered while Cloudflare's `1.1.1.1` returned nothing for
   the same name. Check with
   `dig +short @8.8.8.8 <host>.ts.net` versus `@1.1.1.1`. If public DNS
   answers, the tunnel is fine and it's the resolver — switching that device's
   DNS to 8.8.8.8 fixes it.
3. **Funnel got turned off.** `tailscale --socket=... funnel status`.

If DNS turns out to be unreliable on the networks she actually uses, Funnel is
the wrong tool for her — a real host with a normal domain is worth the few
dollars, since neither of you can debug her carrier's resolver.

### Either way

The link only works while the command is running, so your machine has to be
awake. If you need it reachable when your machine is off, that's when a real
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

- **Speed:** 1×, 1.25×, 1.5×, 2×, 3×, 4× — the browser's native `playbackRate`, so
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
| `BOOKTALKS_TTS_CONCURRENCY` | `16` | Chunks narrated at once; lower it if the TTS service rate limits |
| `BOOKTALKS_MAX_UPLOAD_MB` | `200` | Upload size ceiling |
| `BOOKTALKS_LOCAL_TTS` | `true` | The offline fallback. Off on Render — see [The offline fallback](#the-offline-fallback) |
| `BOOKTALKS_LOCAL_TTS_VOICE` | `en_US-lessac-medium` | Piper voice; `python -m piper.download_voices` lists the rest |
| `BOOKTALKS_LOCAL_TTS_MODEL_DIR` | `{DATA_DIR}/piper` | Where the voice model is downloaded on first use |
| `BOOKTALKS_LOCAL_TTS_CONCURRENCY` | `2` | Pages narrated locally at once. Deliberately low — this is CPU-bound, not network-bound |
| `BOOKTALKS_LOCAL_TTS_FALLBACK_AFTER` | `3` | Service-side failures before switching engines (capped at the document's page count) |
| `BOOKTALKS_LOCAL_TTS_RETRY_EDGE_AFTER_SEC` | `1800` | How long a new document keeps starting on the local engine after an outage |
| `BOOKTALKS_CORS_ORIGINS` | localhost dev ports | Only matters for `vite dev` or a split frontend/backend deploy |
| `BOOKTALKS_STATIC_DIR` | `frontend/dist` | Built UI the API serves, when present |
| `BOOKTALKS_PASSWORD` | unset (no login) | Set to require a password — see [Deploying it publicly](#deploying-it-publicly) |
| `BOOKTALKS_SESSION_SECRET` | the password | Signs session cookies; set separately if sessions should survive a password change |
| `BOOKTALKS_SESSION_DAYS` | `30` | How long a login lasts |
| `BOOKTALKS_SECURE_COOKIES` | `false` | Mark the session cookie HTTPS-only — turn on for any public deployment |
| `BOOKTALKS_COMMIT` | read from git | Build shown by `/api/health`; Render supplies `RENDER_GIT_COMMIT` automatically |
| `BOOKTALKS_AUTH_RATE_LIMIT` | `10` | Failed logins allowed per IP before a 429 |
| `BOOKTALKS_AUTH_RATE_WINDOW_SEC` | `300` | Window the rate limit above resets over |

## The offline fallback

edge-tts depends on a service that can refuse everyone at once, and in
October 2026 it did: Microsoft retired the host edge-tts had always used and
every page of every book failed with a `403`. The
[endpoint patch](#the-edge-tts-endpoint-patch) below fixes that particular
outage, but it is a patch on someone else's unreleased fix, and the exposure
behind it is permanent. A single-engine app converts **nothing** when the
service says no, and all the user sees is "Audio generation failed for every
page."

So there is a second engine — [Piper](https://github.com/OHF-Voice/piper1-gpl),
running entirely on this machine, with no service to have an outage.

### How the switch is triggered

edge-tts is still preferred: better voices, and no local compute. The local
engine takes over only when the evidence says the *service* is broken rather
than the page:

- A failure counts toward the switch only if it **can't be about the page** —
  a `403`/`401`/`429`/`5xx`, a refused websocket handshake, a DNS failure, a
  refused connection. A page that fails after the engine connected and
  answered (empty audio, a bad chunk) is just a failed page, as before.
- It takes **three** such failures (`BOOKTALKS_LOCAL_TTS_FALLBACK_AFTER`), and
  **any success resets the count** — so a connection that drops occasionally
  but mostly works never trips it.
- The threshold is **capped at the document's page count**, so a two-page PDF
  doesn't have to fail three times to qualify. A whole document failing is
  exactly the signal.
- Switching is **one-way within a document**. A book narrated in two
  alternating voices would be worse than one consistent fallback voice.
- Pages that failed *before* the switch are **re-narrated locally** afterwards,
  so a book doesn't permanently open with a few silent pages.
- Once edge-tts has been written off, the next document **starts local** for
  30 minutes (`BOOKTALKS_LOCAL_TTS_RETRY_EDGE_AFTER_SEC`) rather than
  rediscovering the outage every time — then tries edge-tts again.

Which engine actually spoke a book is recorded against it and shown in the UI
(`engine` on `/api/documents/{id}`: `edge-tts`, `piper`, or `edge-tts+piper`
for one that switched mid-book). A locally narrated book sounds different, so
the player says "Offline voice" instead of naming a voice the listener never
hears. `/api/health` reports `local_tts` so you can tell from outside whether
a deployment has a fallback at all.

### What it costs

Measured on the same 40-page PDF (2,948 characters per page), one M-series
Mac, `BOOKTALKS_LOCAL_TTS_CONCURRENCY=2`:

| | edge-tts | Piper (local) |
|---|---|---|
| Wall clock, 40 pages | **18.7 s** | 444 s |
| pages/sec | **2.14** | 0.090 |
| Faster than realtime | **386x** | 13.7x |
| Peak RSS, whole server | **102 MB** | 822 MB |
| Needs network | yes | **no** |

So the fallback is roughly **24x slower** and costs about **700 MB more RAM**.
It is still comfortably faster than listening: 13.7x realtime means a 10-hour
audiobook renders in about 45 minutes. That is the trade — a slower, heavier,
slightly-worse-sounding book instead of no book.

Output is byte-compatible with the edge-tts path: both are 24 kHz mono mp3 at
48 kbps, measured at −21.2 dB mean (edge-tts) and −21.4 dB (Piper), so a
document that switched engines part-way doesn't change format or volume at the
join. Piper's model runs at 22050 Hz and is resampled during the mp3 encode —
it has to be, because pages are concatenated with `-c copy` and never decoded.

### Installing it

It's an **optional dependency**, because onnxruntime and numpy are ~190 MB and
the engine needs real memory:

```bash
backend/.venv/bin/pip install -r backend/requirements-local-tts.txt
```

`scripts/share.sh` does this automatically, so the Mac deployment has it
without being asked. Where it isn't installed, `get_engine()` returns plain
edge-tts and the app behaves exactly as it did before the fallback existed —
there is no crash and no degraded path to hit.

The **voice model is never committed**: 63 MB of weights has no business in
git. The first narration that needs it downloads it (about four seconds) into
`BOOKTALKS_LOCAL_TTS_MODEL_DIR`, which is under `data/` and already
gitignored. Every later page, document and restart reuses the file.

### It is not viable on Render's free tier

Free instances get 0.1 CPU, 512 MB RAM and no persistent disk. All three rule
it out, and the numbers aren't close:

- **Memory** — the engine peaks at ~820 MB with the server attached, against a
  512 MB cap for the whole instance. It would OOM.
- **CPU** — 0.058 RTF on a full core becomes ~0.58 at 0.1 CPU, i.e. only
  ~1.7x realtime. A 300-page book would take about **six hours**, past any
  reasonable wait and past the free plan's spin-down.
- **Disk** — nothing persists, so the 63 MB model would re-download on every
  restart.

Rather than ship something that OOMs, piper-tts is simply **not in the Docker
image**, and `render.yaml` sets `BOOKTALKS_LOCAL_TTS=false` so the startup log
says so plainly instead of leaving it looking like it might kick in. Render
keeps working exactly as it does today, on edge-tts alone. The Mac deployment
behind the Tailscale Funnel is where the fallback actually runs — which is
also the deployment that gets used.

### Swapping either engine

Every text-to-speech call goes through one interface in `backend/app/tts.py`.
To add or replace an engine, implement a class with
`async def synthesize(self, text, out_path) -> float` (returning the duration
in seconds) and return it from `get_engine()`. Nothing else in the codebase
knows which engine is in use.

Kokoro-82M was the first choice for the local engine and was measured against
Piper before being dropped — on the same page of prose it was **4.6x slower**
(3.7x realtime vs 17.3x) and used **~1 GB of RAM** against Piper's 387 MB,
from a model 2–6x the size. Its voice is nicer, but not at that price in a
project whose recurring complaint has been narration speed.

### If espeak-ng says a file is missing

If the local engine fails with something like:

```
Error processing file '/Users/runner/work/piper1-gpl/.../espeak-ng-data/phontab'
```

that is **not** a broken package, and pinning a different piper-tts version
won't help. espeak-ng formats its data directory into a fixed 160-byte buffer;
anything longer is silently truncated, fails its own existence check, and
falls back to the path baked in on whatever machine built the wheel. Measured:
155 characters works, 160 doesn't. `tts.py` works around it by pointing espeak
at a short symlink, but if you see it anyway, the fix is a shorter path (a
shallower `BOOKTALKS_DATA_DIR` or virtualenv), not a different version.

### The edge-tts endpoint patch

In October 2026 Microsoft began answering `403` to every speech-synthesis
request on the host edge-tts has always used
(`speech.platform.bing.com/.../readaloud/edge/v1`). Edge 155 moved synthesis
to `api.msedgeservices.com`, which accepts the same trusted client token as
`Ocp-Apim-Subscription-Key`, and the old host is being retired.

The symptom is total and looks like a problem with your PDF or your host: every
page of every book fails within seconds with
`edge-tts failed after 3 attempts: 403`, and the document ends up `failed`
with "Audio generation failed for every page." It is neither — the file is
fine and so is the network.

`backend/app/edge_endpoint.py` repoints the websocket, and `tts.py` applies it
before the first call. edge-tts 7.2.8 — the newest release — still ships the
dead URL; the upstream fix is open but unreleased
([rany2/edge-tts#490](https://github.com/rany2/edge-tts/issues/490),
[PR #491](https://github.com/rany2/edge-tts/pull/491)).

The patch is conditional, so it retires itself: once a release carries the new
host, the check stops matching and `apply()` becomes a no-op. When you bump
the pin in `backend/requirements.txt`, run the tests — if
`test_apply_is_idempotent` still passes, upstream has caught up and
`edge_endpoint.py` can be deleted along with its tests and the
`apply()` call in `tts.py`.

Only synthesis moved; the voice list is still served by the legacy host, which
is why `--list-voices` kept working throughout and made the breakage look
like something other than what it was.

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

88 tests covering text cleanup, chunking, page-offset maths, upload validation,
range requests, playback state, deletion, voice selection, metadata-derived
durations, the edge-tts endpoint patch, and the auth gate (login, logout,
rate limiting, forged/expired/replayed cookies).

For the [offline fallback](#the-offline-fallback): engine selection, which
failures count as service-side (and which paths must *not* be misread as HTTP
status codes), the three-strikes trigger, successes resetting the count, the
threshold cap on short documents, pages racing the switch, one-way switching
within a document, the outage latch expiring, re-narration of pages lost
before a switch, and what gets recorded against the document.

TTS is stubbed throughout and no model is ever loaded, so the suite needs no
network and runs the same whether or not piper-tts is installed.

## Layout

```
backend/app/
  main.py       API endpoints, range streaming, static SPA hosting
  auth.py       Password gate: signed session cookies, rate limiting
  pipeline.py   Background job: extract → narrate → concatenate
  pdf_text.py   Extraction and cleanup
  tts.py        TTS interface, edge-tts engine, local Piper engine, fallback
  audio.py      ffmpeg/ffprobe: durations, silence, concatenation, PCM encode
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
backend/requirements-local-tts.txt  Optional offline TTS engine (not in the image)
```

## Not in v1

Multi-user accounts (there's a password, not accounts — see
[Deploying it publicly](#deploying-it-publicly)), OCR for scanned PDFs, a voice
picker, word-level highlighting, and editing extracted text before narration.
