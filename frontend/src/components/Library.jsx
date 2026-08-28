import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "./Icons.jsx";
import { StatusBadge, isWorking } from "./StatusBadge.jsx";
import { ConfirmDialog } from "./ConfirmDialog.jsx";
import { UploadDropzone } from "./UploadDropzone.jsx";
import { api } from "../lib/api.js";
import { formatDate, formatDuration, stripExtension } from "../lib/format.js";

const POLL_MS = 2500;

export function Library({ onOpen, onToast }) {
  const [documents, setDocuments] = useState(null);
  const [error, setError] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const timerRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const list = await api.listDocuments();
      setDocuments(list);
      setError(null);
      return list;
    } catch (err) {
      setError(err.message);
      return null;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      const list = await refresh();
      if (cancelled) return;
      // Keep polling while something is being converted — and keep retrying if
      // the request failed, so a restarted backend recovers on its own.
      if (list === null || list.some((doc) => isWorking(doc.status))) {
        timerRef.current = setTimeout(tick, list === null ? POLL_MS * 2 : POLL_MS);
      }
    }

    tick();
    return () => {
      cancelled = true;
      clearTimeout(timerRef.current);
    };
  }, [refresh, reloadKey]);

  function handleUploaded(created) {
    onToast(`Converting “${stripExtension(created.filename)}”…`);
    // Restarts the effect above, which reloads the list and resumes polling.
    setReloadKey((key) => key + 1);
  }

  async function confirmDelete() {
    const doc = pendingDelete;
    setPendingDelete(null);
    try {
      await api.deleteDocument(doc.id);
      onToast(`Deleted “${stripExtension(doc.filename)}”`);
      setReloadKey((key) => key + 1);
    } catch (err) {
      onToast(err.message);
    }
  }

  return (
    <>
      <div className="stack stack-32">
        <section className="stack stack-12">
          <h1 className="t-large-title">Library</h1>
          <p className="t-subhead secondary" style={{ marginTop: -4 }}>
            Drop in a PDF and BookTalks narrates it, page by page.
          </p>
          <UploadDropzone onUploaded={handleUploaded} onError={(message) => onToast(message)} />
        </section>

        <section className="stack stack-8">
          <div className="section-header">
            <h2 className="t-headline">Your audiobooks</h2>
            {documents?.length > 0 && (
              <span className="t-footnote secondary">
                {documents.length} {documents.length === 1 ? "item" : "items"}
              </span>
            )}
          </div>

          {error && (
            <div className="banner" role="alert">
              <Icon.Warning width={20} height={20} />
              <span className="banner__text">{error}</span>
            </div>
          )}

          <div className="card">
            {documents === null && error ? (
              <div className="empty">
                <Icon.Warning width={28} height={28} strokeWidth={1.6} />
                <p className="t-headline" style={{ color: "var(--label)" }}>
                  Can't load your library
                </p>
                <p className="t-subhead">Retrying automatically…</p>
              </div>
            ) : documents === null ? (
              <div className="empty">
                <span className="spinner" style={{ color: "var(--label-secondary)" }} />
                <span className="t-subhead">Loading…</span>
              </div>
            ) : documents.length === 0 ? (
              <div className="empty">
                <Icon.Waveform width={30} height={30} strokeWidth={1.6} />
                <p className="t-headline" style={{ color: "var(--label)" }}>
                  Nothing here yet
                </p>
                <p className="t-subhead">Your converted PDFs will show up in this list.</p>
              </div>
            ) : (
              <div className="list" role="list">
                {documents.map((doc) => (
                  <DocumentRow
                    key={doc.id}
                    doc={doc}
                    onOpen={() => onOpen(doc.id)}
                    onDelete={() => setPendingDelete(doc)}
                  />
                ))}
              </div>
            )}
          </div>
        </section>
      </div>

      {pendingDelete && (
        <ConfirmDialog
          title={`Delete “${stripExtension(pendingDelete.filename)}”?`}
          message="This removes the PDF and its audio from this computer. It can't be undone."
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </>
  );
}

function DocumentRow({ doc, onOpen, onDelete }) {
  const title = stripExtension(doc.filename);
  const working = isWorking(doc.status);
  const percent =
    doc.page_count > 0 ? Math.round((doc.pages_done / doc.page_count) * 100) : 0;

  return (
    <div className="row row--interactive" role="listitem">
      <button type="button" className="row__open" onClick={onOpen}>
        <span className="doc-icon" aria-hidden="true">
          <Icon.Doc width={22} height={22} />
        </span>
        <span className="row__main">
          <span className="row__title">{title}</span>
          <span className="row__meta">
            <StatusBadge status={doc.status} />
            {doc.status === "ready" && <span>{formatDuration(doc.total_duration_sec)}</span>}
            {working && doc.page_count > 0 && (
              <span className="tabular">
                {doc.pages_done} of {doc.page_count} pages
              </span>
            )}
            {doc.page_count > 0 && !working && doc.status !== "failed" && (
              <span className="meta-pages">{doc.page_count} pages</span>
            )}
            <span className="meta-date">{formatDate(doc.upload_date)}</span>
          </span>
          {working && (
            <span
              className={`progress${doc.status === "generating_audio" ? "" : " progress--indeterminate"}`}
              style={{ marginTop: 8 }}
              role="progressbar"
              aria-valuenow={doc.status === "generating_audio" ? percent : undefined}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`Converting ${title}`}
            >
              <span
                className="progress__fill"
                style={
                  doc.status === "generating_audio" ? { width: `${percent}%` } : undefined
                }
              />
            </span>
          )}
          {doc.status === "failed" && doc.error_message && (
            <span className="row__meta" style={{ color: "var(--red)" }}>
              {doc.error_message}
            </span>
          )}
        </span>
        <Icon.ChevronRight className="chevron" width={18} height={18} strokeWidth={2.2} />
      </button>
      <div className="row__actions">
        <button
          type="button"
          className="btn btn--icon btn--danger"
          onClick={onDelete}
          aria-label={`Delete ${title}`}
          title="Delete"
        >
          <Icon.Trash width={19} height={19} />
        </button>
      </div>
    </div>
  );
}
