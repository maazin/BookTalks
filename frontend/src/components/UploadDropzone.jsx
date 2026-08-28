import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icons.jsx";
import { VoicePicker } from "./VoicePicker.jsx";
import { api } from "../lib/api.js";
import { stripExtension } from "../lib/format.js";

const LAST_VOICE_KEY = "booktalks_last_voice";

export function UploadDropzone({ onUploaded, onError }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState(null);

  // The file waiting on a voice choice before it actually uploads — narration
  // is pre-rendered server-side, so the voice has to be picked up front, not
  // changed later like speed or volume can be.
  const [pendingFile, setPendingFile] = useState(null);
  const [voiceCatalog, setVoiceCatalog] = useState(null); // { default, voices } | "error"
  const [selectedVoice, setSelectedVoice] = useState(null);

  const uploading = progress !== null;

  useEffect(() => {
    api
      .listVoices()
      .then((catalog) => {
        setVoiceCatalog(catalog);
        const remembered = localStorage.getItem(LAST_VOICE_KEY);
        const known = remembered && catalog.voices.some((v) => v.short_name === remembered);
        setSelectedVoice(known ? remembered : catalog.default);
      })
      .catch(() => setVoiceCatalog("error"));
  }, []);

  function chooseFile(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      onError("That file isn't a PDF. BookTalks reads PDFs only.");
      return;
    }
    setPendingFile(file);
  }

  function cancelPending() {
    setPendingFile(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  async function confirmUpload() {
    const file = pendingFile;
    setPendingFile(null);
    setProgress(0);
    try {
      const created = await api.upload(file, selectedVoice, setProgress);
      if (selectedVoice) localStorage.setItem(LAST_VOICE_KEY, selectedVoice);
      onUploaded(created);
    } catch (error) {
      onError(error.message);
    } finally {
      setProgress(null);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function onDrop(event) {
    event.preventDefault();
    setDragging(false);
    if (uploading || pendingFile) return;
    chooseFile(event.dataTransfer.files?.[0]);
  }

  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        className="visually-hidden"
        onChange={(event) => chooseFile(event.target.files?.[0])}
      />

      {pendingFile ? (
        <div className="card card--pad card--overflow-visible stack stack-16">
          <div className="row" style={{ padding: 0 }}>
            <span className="doc-icon" aria-hidden="true">
              <Icon.Doc width={22} height={22} />
            </span>
            <span className="row__main">
              <span className="row__title">{stripExtension(pendingFile.name)}</span>
              <span className="row__meta">
                <span>{(pendingFile.size / (1024 * 1024)).toFixed(1)} MB</span>
              </span>
            </span>
          </div>

          <div className="stack stack-8">
            <span className="t-footnote secondary">Voice</span>
            {voiceCatalog === null ? (
              <div className="voice-picker__trigger" aria-busy="true">
                <span className="spinner" />
                <span className="t-subhead secondary">Loading voices…</span>
              </div>
            ) : voiceCatalog === "error" ? (
              <p className="t-footnote secondary">
                Couldn't load the voice list — this will narrate with the default voice.
              </p>
            ) : (
              <VoicePicker
                voices={voiceCatalog.voices}
                value={selectedVoice}
                onChange={setSelectedVoice}
              />
            )}
          </div>

          <div style={{ display: "flex", gap: 10 }}>
            <button type="button" className="btn" style={{ flex: 1 }} onClick={cancelPending}>
              Cancel
            </button>
            <button type="button" className="btn btn--primary" style={{ flex: 1 }} onClick={confirmUpload}>
              Convert
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          className="dropzone"
          data-dragging={dragging}
          disabled={uploading}
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault();
            if (!uploading) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <span className="dropzone__glyph">
            <Icon.UploadDoc width={26} height={26} strokeWidth={1.9} />
          </span>
          <span className="t-headline">{uploading ? "Uploading…" : "Add a PDF"}</span>
          <span className="t-subhead secondary">
            {uploading ? `${Math.round(progress * 100)}% sent` : "Drag one here, or click to choose a file"}
          </span>
          {uploading && (
            <span className="progress" style={{ maxWidth: 260, marginTop: 6 }}>
              <span className="progress__fill" style={{ width: `${progress * 100}%` }} />
            </span>
          )}
        </button>
      )}
    </div>
  );
}
