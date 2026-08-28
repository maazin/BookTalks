import { useRef, useState } from "react";
import { Icon } from "./Icons.jsx";
import { api } from "../lib/api.js";

export function UploadDropzone({ onUploaded, onError }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState(null);

  const uploading = progress !== null;

  async function send(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      onError("That file isn't a PDF. BookTalks reads PDFs only.");
      return;
    }
    setProgress(0);
    try {
      const created = await api.upload(file, setProgress);
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
    if (uploading) return;
    send(event.dataTransfer.files?.[0]);
  }

  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        className="visually-hidden"
        onChange={(event) => send(event.target.files?.[0])}
      />
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
        <span className="t-headline">
          {uploading ? "Uploading…" : "Add a PDF"}
        </span>
        <span className="t-subhead secondary">
          {uploading
            ? `${Math.round(progress * 100)}% sent`
            : "Drag one here, or click to choose a file"}
        </span>
        {uploading && (
          <span className="progress" style={{ maxWidth: 260, marginTop: 6 }}>
            <span className="progress__fill" style={{ width: `${progress * 100}%` }} />
          </span>
        )}
      </button>
    </div>
  );
}
