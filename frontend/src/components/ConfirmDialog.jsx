import { useEffect, useRef } from "react";

/** Modal confirmation for destructive actions, per HIG guidance that hard-to-
 *  recover actions are confirmed. Escape cancels; focus starts on Cancel. */
export function ConfirmDialog({ title, message, confirmLabel = "Delete", onConfirm, onCancel }) {
  const cancelRef = useRef(null);

  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (event) => {
      if (event.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className="scrim" onClick={onCancel}>
      <div
        className="dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-message"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="confirm-title" className="t-headline">{title}</h2>
        <p id="confirm-message" className="t-subhead secondary" style={{ marginTop: 6 }}>
          {message}
        </p>
        <div className="dialog__actions">
          <button type="button" className="btn" ref={cancelRef} onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="btn btn--destructive" onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
