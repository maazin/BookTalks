export function formatTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`;
}

/** Spoken-friendly duration for labels and screen readers: "1 hr 12 min". */
export function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.round((total % 3600) / 60);
  if (hours > 0) return minutes ? `${hours} hr ${minutes} min` : `${hours} hr`;
  if (minutes > 0) return `${minutes} min`;
  return `${total} sec`;
}

export function formatDate(value) {
  if (!value) return "";
  // SQLite hands back "YYYY-MM-DD HH:MM:SS" in UTC.
  const iso = value.includes("T") ? value : `${value.replace(" ", "T")}Z`;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: date.getFullYear() === new Date().getFullYear() ? undefined : "numeric",
  });
}

export function stripExtension(filename) {
  return (filename || "").replace(/\.pdf$/i, "");
}

/** "en-US-AriaNeural" -> "Aria". Mirrors the backend's voices._display_name
 *  so the player can show a voice's name without a second /api/voices fetch. */
export function voiceDisplayName(shortName) {
  const match = /^[a-z]{2,3}-[A-Z]{2}-(.+?)(?:Multilingual)?Neural\d*$/.exec(shortName || "");
  return match ? match[1] : shortName || "";
}

/** Who narrated this document, in words. "" when there's nothing to say yet.
 *
 *  Not simply the voice name, because `voice` records the voice that was
 *  *asked for* — an edge-tts voice, chosen at upload. If the service was down
 *  and the local engine spoke the book instead, that name is of a voice the
 *  listener never hears, so printing it would be a plain lie. A locally
 *  narrated book sounds noticeably different, and "why does this one sound
 *  like that?" deserves an answer on screen.
 *
 *  A null engine means the document predates the fallback, which is not the
 *  same as knowing edge-tts narrated it — but the voice name was accurate
 *  then, so it's still the right thing to show. */
export function narrationLabel(doc) {
  const voice = voiceDisplayName(doc?.voice);
  switch (doc?.engine) {
    case "piper":
      return "Offline voice";
    // Tripped part-way: the book genuinely holds both, in order.
    case "edge-tts+piper":
      return voice ? `${voice}, then offline voice` : "Partly offline voice";
    default:
      return voice;
  }
}
