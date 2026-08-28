import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./Icons.jsx";
import { ConfirmDialog } from "./ConfirmDialog.jsx";
import { StatusBadge, isWorking, statusLabel } from "./StatusBadge.jsx";
import { api } from "../lib/api.js";
import { formatDuration, formatTime, stripExtension } from "../lib/format.js";

const SPEEDS = [1, 1.25, 1.5, 2];
const SKIP_SECONDS = 15;
const SAVE_EVERY_MS = 10000;
const STATUS_POLL_MS = 2500;

export function Player({ documentId, onToast, onExit }) {
  const [doc, setDoc] = useState(null);
  const [pages, setPages] = useState([]);
  const [error, setError] = useState(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const audioRef = useRef(null);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [rate, setRate] = useState(1);
  const [buffering, setBuffering] = useState(false);

  // Resuming needs two things that arrive in either order: the saved position
  // from the API, and an <audio> element that knows its duration. Whichever
  // lands second triggers the restore.
  const [savedState, setSavedState] = useState(null);
  const [metadataReady, setMetadataReady] = useState(false);
  const restoredRef = useRef(false);

  /* --- load document, polling while it's still being converted ----------- */
  useEffect(() => {
    let cancelled = false;
    let timer;

    async function load() {
      try {
        const detail = await api.getDocument(documentId);
        if (cancelled) return;
        setDoc(detail);
        setError(null);
        if (isWorking(detail.status)) {
          timer = setTimeout(load, STATUS_POLL_MS);
        }
      } catch (err) {
        if (cancelled) return;
        setError(err.message);
        // A missing document stays missing; anything else is worth retrying,
        // so a restarted backend doesn't leave a dead screen behind.
        if (err.status !== 404) {
          timer = setTimeout(load, STATUS_POLL_MS * 2);
        }
      }
    }

    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [documentId]);

  /* --- once ready, fetch the page timeline and the saved position -------- */
  const ready = doc?.status === "ready";

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;

    (async () => {
      try {
        const [pageList, playback] = await Promise.all([
          api.getPages(documentId),
          api.getPlayback(documentId),
        ]);
        if (cancelled) return;
        setPages(pageList);
        const savedRate = SPEEDS.includes(playback.playback_rate)
          ? playback.playback_rate
          : 1;
        setRate(savedRate);
        if (audioRef.current) audioRef.current.playbackRate = savedRate;
        setSavedState({ position: playback.position_sec || 0, rate: savedRate });
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [ready, documentId]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !metadataReady || !savedState || restoredRef.current) return;
    audio.playbackRate = savedState.rate;
    // Ignore a position parked at the very end — that's a finished book, and
    // reopening it should start it over rather than land on silence.
    if (savedState.position > 0 && savedState.position < audio.duration - 1) {
      audio.currentTime = savedState.position;
      setCurrentTime(savedState.position);
    }
    restoredRef.current = true;
  }, [metadataReady, savedState]);

  /* --- persist position ------------------------------------------------- */
  const save = useCallback(
    (keepalive = false) => {
      const audio = audioRef.current;
      if (!audio || !ready || !restoredRef.current) return;
      api
        .savePlayback(documentId, audio.currentTime || 0, audio.playbackRate || 1, keepalive)
        .catch(() => {
          /* a dropped autosave isn't worth interrupting playback for */
        });
    },
    [documentId, ready]
  );

  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => save(), SAVE_EVERY_MS);
    return () => clearInterval(timer);
  }, [playing, save]);

  useEffect(() => {
    const onHide = () => save(true);
    const onVisibility = () => {
      if (document.visibilityState === "hidden") onHide();
    };
    window.addEventListener("pagehide", onHide);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pagehide", onHide);
      document.removeEventListener("visibilitychange", onVisibility);
      save(true);
    };
  }, [save]);

  /* --- transport -------------------------------------------------------- */
  const togglePlay = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      audio.play().catch((err) => onToast(`Couldn't start playback: ${err.message}`));
    } else {
      audio.pause();
    }
  }, [onToast]);

  const seekTo = useCallback(
    (seconds) => {
      const audio = audioRef.current;
      if (!audio) return;
      const limit = duration || audio.duration || 0;
      const next = Math.min(Math.max(seconds, 0), Math.max(limit - 0.05, 0));
      audio.currentTime = next;
      setCurrentTime(next);
    },
    [duration]
  );

  const skip = useCallback(
    (delta) => seekTo((audioRef.current?.currentTime || 0) + delta),
    [seekTo]
  );

  const jumpToPage = useCallback(
    (page) => {
      seekTo(page.start_time_sec || 0);
      audioRef.current?.play().catch(() => {});
    },
    [seekTo]
  );

  function changeRate(next) {
    setRate(next);
    if (audioRef.current) audioRef.current.playbackRate = next;
    save();
  }

  /* --- keyboard shortcuts ----------------------------------------------- */
  useEffect(() => {
    if (!ready) return;
    function onKey(event) {
      const tag = event.target.tagName;
      // The scrubber is an <input type="range">, but arrows there should still
      // mean "skip 15 seconds" rather than nudge by a tenth of a second.
      const isTextField =
        tag === "TEXTAREA" || (tag === "INPUT" && event.target.type !== "range");
      if (isTextField || event.metaKey || event.ctrlKey) return;
      if (event.code === "Space" || event.key === "k") {
        event.preventDefault();
        togglePlay();
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        skip(-SKIP_SECONDS);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        skip(SKIP_SECONDS);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ready, togglePlay, skip]);

  /* --- derived ---------------------------------------------------------- */
  const totalDuration = duration || doc?.total_duration_sec || 0;
  const currentPage = useMemo(() => {
    if (!pages.length) return null;
    let found = pages[0];
    for (const page of pages) {
      if (page.start_time_sec !== null && page.start_time_sec <= currentTime + 0.25) {
        found = page;
      } else break;
    }
    return found;
  }, [pages, currentTime]);

  const title = doc ? stripExtension(doc.filename) : "";

  /* --- render ----------------------------------------------------------- */
  if (error) {
    return (
      <div className="stack stack-16">
        <div className="banner" role="alert">
          <Icon.Warning width={20} height={20} />
          <span className="banner__text">{error}</span>
        </div>
      </div>
    );
  }

  if (!doc) {
    return (
      <div className="stack stack-16">
        <div className="card">
          <div className="empty">
            <span className="spinner" style={{ color: "var(--label-secondary)" }} />
            <span className="t-subhead">Loading…</span>
          </div>
        </div>
      </div>
    );
  }

  async function deleteDocument() {
    setConfirmingDelete(false);
    try {
      await api.deleteDocument(documentId);
      onToast(`Deleted “${title}”`);
      onExit();
    } catch (err) {
      onToast(err.message);
    }
  }

  if (doc.status !== "ready") {
    return (
      <div className="stack stack-16">
        <ProcessingCard
          doc={doc}
          title={title}
          onDelete={() => setConfirmingDelete(true)}
          onExit={onExit}
        />
        {confirmingDelete && (
          <ConfirmDialog
            title={`Delete “${title}”?`}
            message="This removes the PDF from this computer. It can't be undone."
            onConfirm={deleteDocument}
            onCancel={() => setConfirmingDelete(false)}
          />
        )}
      </div>
    );
  }

  const failedPages = pages.filter((page) => page.status === "failed").length;

  return (
    <div className="stack stack-16">
      <audio
        ref={audioRef}
        src={api.audioUrl(documentId)}
        preload="metadata"
        onLoadedMetadata={(event) => {
          const audio = event.currentTarget;
          setDuration(Number.isFinite(audio.duration) ? audio.duration : 0);
          audio.playbackRate = rate;
          setMetadataReady(true);
        }}
        onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
        onPlay={() => setPlaying(true)}
        onPause={() => {
          setPlaying(false);
          save();
        }}
        onEnded={() => {
          setPlaying(false);
          save();
        }}
        onWaiting={() => setBuffering(true)}
        onPlaying={() => setBuffering(false)}
        onCanPlay={() => setBuffering(false)}
        onError={() => setError("The audio file couldn't be loaded.")}
      />

      <section className="card card--pad stack stack-16">
        <div className="player-hero">
          <div className="artwork" aria-hidden="true">
            <Icon.Waveform width={54} height={54} strokeWidth={1.5} />
          </div>
          <h1 className="t-title">{title}</h1>
          <p className="t-subhead secondary">
            {doc.page_count} pages · {formatDuration(doc.total_duration_sec)}
            {currentPage ? ` · Page ${currentPage.page_number}` : ""}
          </p>
        </div>

        <div className="scrubber">
          <input
            className="slider"
            type="range"
            min={0}
            max={Math.max(totalDuration, 0.1)}
            step={0.1}
            value={Math.min(currentTime, totalDuration || 0)}
            style={{
              "--fill-percent": `${totalDuration ? (currentTime / totalDuration) * 100 : 0}%`,
            }}
            onChange={(event) => seekTo(Number(event.target.value))}
            aria-label="Seek"
            aria-valuetext={`${formatTime(currentTime)} of ${formatTime(totalDuration)}`}
          />
          <div className="scrubber__times">
            <span>{formatTime(currentTime)}</span>
            <span>-{formatTime(Math.max(totalDuration - currentTime, 0))}</span>
          </div>
        </div>

        <div className="transport">
          <button
            type="button"
            className="transport__btn"
            onClick={() => skip(-SKIP_SECONDS)}
            aria-label="Skip back 15 seconds"
            title="Back 15s (←)"
          >
            <Icon.Back15 width={28} height={28} />
          </button>
          <button
            type="button"
            className="transport__btn transport__btn--play"
            onClick={togglePlay}
            aria-label={playing ? "Pause" : "Play"}
            title={playing ? "Pause (space)" : "Play (space)"}
          >
            {buffering ? (
              <span className="spinner" style={{ width: 22, height: 22, borderWidth: 3 }} />
            ) : playing ? (
              <Icon.Pause width={30} height={30} />
            ) : (
              <Icon.Play width={30} height={30} />
            )}
          </button>
          <button
            type="button"
            className="transport__btn"
            onClick={() => skip(SKIP_SECONDS)}
            aria-label="Skip forward 15 seconds"
            title="Forward 15s (→)"
          >
            <Icon.Forward15 width={28} height={28} />
          </button>
        </div>

        <div className="stack stack-8">
          <span className="t-footnote secondary" id="speed-label">
            Playback speed
          </span>
          <div className="segmented" role="radiogroup" aria-labelledby="speed-label">
            {SPEEDS.map((speed) => (
              <button
                key={speed}
                type="button"
                role="radio"
                aria-checked={rate === speed}
                className="segmented__option"
                onClick={() => changeRate(speed)}
              >
                {speed}×
              </button>
            ))}
          </div>
        </div>

        <p className="t-footnote secondary kbd-hint" style={{ textAlign: "center" }}>
          Space plays and pauses · ← → skip 15 seconds
        </p>
      </section>

      {failedPages > 0 && (
        <div className="banner" role="status">
          <Icon.Warning width={20} height={20} />
          <span className="banner__text">
            {failedPages} {failedPages === 1 ? "page" : "pages"} couldn't be narrated and
            {failedPages === 1 ? " was" : " were"} skipped.
          </span>
        </div>
      )}

      <PageJump pages={pages} currentPage={currentPage} onJump={jumpToPage} />
    </div>
  );
}

function ProcessingCard({ doc, title, onDelete, onExit }) {
  const percent =
    doc.page_count > 0 ? Math.round((doc.pages_done / doc.page_count) * 100) : 0;
  const failed = doc.status === "failed";

  return (
    <section className="card card--pad stack stack-16" aria-live="polite">
      <div className="player-hero">
        <div
          className="artwork"
          aria-hidden="true"
          style={failed ? { background: "var(--fill-strong)", color: "var(--label-secondary)" } : undefined}
        >
          {failed ? (
            <Icon.Warning width={48} height={48} strokeWidth={1.5} />
          ) : (
            <Icon.Clock width={48} height={48} strokeWidth={1.5} />
          )}
        </div>
        <h1 className="t-title">{title}</h1>
        <StatusBadge status={doc.status} />
      </div>

      {failed ? (
        <>
          <p className="t-body secondary" style={{ textAlign: "center" }}>
            {doc.error_message || "Something went wrong while converting this PDF."}
          </p>
          <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
            <button type="button" className="btn" onClick={onExit}>
              Back to library
            </button>
            <button type="button" className="btn btn--danger" onClick={onDelete}>
              Delete
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="t-body secondary" style={{ textAlign: "center" }}>
            {doc.status === "generating_audio"
              ? `Narrated ${doc.pages_done} of ${doc.page_count} pages. You can leave this page — it keeps going.`
              : doc.status === "pending"
                ? "Waiting for the book ahead of it to finish."
                : "Reading the text out of your PDF…"}
          </p>
          <div
            className={`progress${doc.status === "generating_audio" ? "" : " progress--indeterminate"}`}
            role="progressbar"
            aria-valuenow={doc.status === "generating_audio" ? percent : undefined}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`${statusLabel(doc.status)}, ${percent}% complete`}
          >
            <div
              className="progress__fill"
              style={doc.status === "generating_audio" ? { width: `${percent}%` } : undefined}
            />
          </div>
        </>
      )}
    </section>
  );
}

const PageJump = memo(function PageJump({ pages, currentPage, onJump }) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return pages;
    return pages.filter(
      (page) =>
        String(page.page_number) === term ||
        String(page.page_number).startsWith(term) ||
        (page.preview || "").toLowerCase().includes(term)
    );
  }, [pages, query]);

  if (!pages.length) return null;

  return (
    <section className="card stack">
      <div style={{ padding: "16px 16px 12px" }} className="stack stack-12">
        <div className="section-header" style={{ padding: 0 }}>
          <h2 className="t-headline">Jump to a page</h2>
          <span className="t-footnote secondary">{pages.length} pages</span>
        </div>
        <label className="search">
          <Icon.Search width={17} height={17} aria-hidden="true" />
          <input
            type="search"
            value={query}
            placeholder="Page number or a phrase"
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Search pages"
          />
        </label>
      </div>

      <div className="page-list">
        {filtered.length === 0 ? (
          <div className="empty" style={{ padding: 28 }}>
            <span className="t-subhead">No pages match “{query}”.</span>
          </div>
        ) : (
          filtered.map((page) => {
            const unavailable = page.status !== "done";
            return (
              <button
                key={page.page_number}
                type="button"
                className="page-row"
                aria-current={currentPage?.page_number === page.page_number}
                onClick={() => onJump(page)}
                disabled={unavailable && page.start_time_sec === null}
              >
                <span className="page-row__num">{page.page_number}</span>
                <span className="page-row__preview">
                  {unavailable ? "No audio for this page" : page.preview || "—"}
                </span>
                <span className="page-row__time">{formatTime(page.start_time_sec || 0)}</span>
              </button>
            );
          })
        )}
      </div>
    </section>
  );
});
