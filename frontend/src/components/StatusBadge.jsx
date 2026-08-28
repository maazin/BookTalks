import { Icon } from "./Icons.jsx";

/* Status is conveyed by icon + words as well as colour, so it still reads for
   anyone who can't distinguish the hues. */
const STATUS = {
  pending: { label: "Queued", tone: "working", spin: true },
  extracting: { label: "Reading pages", tone: "working", spin: true },
  generating_audio: { label: "Recording", tone: "working", spin: true },
  ready: { label: "Ready", tone: "ready" },
  failed: { label: "Failed", tone: "failed" },
};

export function StatusBadge({ status }) {
  const info = STATUS[status] || { label: status, tone: "" };
  return (
    <span className={`badge badge--${info.tone}`}>
      {info.spin ? (
        <span className="spinner" aria-hidden="true" />
      ) : info.tone === "ready" ? (
        <Icon.Check width={13} height={13} strokeWidth={2.6} />
      ) : (
        <Icon.Warning width={13} height={13} strokeWidth={2.2} />
      )}
      {info.label}
    </span>
  );
}

export function statusLabel(status) {
  return (STATUS[status] || { label: status }).label;
}

export const isWorking = (status) =>
  status === "pending" || status === "extracting" || status === "generating_audio";
