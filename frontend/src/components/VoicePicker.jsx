import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./Icons.jsx";

/** A voice, chosen once at upload time — narration is pre-rendered, so unlike
 *  speed or volume this can't be changed after the fact without re-converting
 *  the whole document. Renders as a button that expands into a searchable
 *  list, since edge-tts offers ~300 voices and a plain <select> would be
 *  unusable at that size. */
export function VoicePicker({ voices, value, onChange, disabled }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const searchRef = useRef(null);
  const rootRef = useRef(null);

  const selected = voices.find((v) => v.short_name === value) || null;

  useEffect(() => {
    if (open) searchRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(event) {
      if (rootRef.current && !rootRef.current.contains(event.target)) {
        setOpen(false);
      }
    }
    function onKey(event) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const filtered = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return voices;
    return voices.filter(
      (v) =>
        v.display_name.toLowerCase().includes(term) ||
        v.locale_name.toLowerCase().includes(term) ||
        v.locale.toLowerCase().includes(term)
    );
  }, [voices, query]);

  return (
    <div className="voice-picker" ref={rootRef}>
      <button
        type="button"
        className="voice-picker__trigger"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="voice-picker__avatar" aria-hidden="true">
          {selected ? selected.display_name[0] : "?"}
        </span>
        <span className="voice-picker__label">
          <span className="t-subhead" style={{ color: "var(--label)" }}>
            {selected ? selected.display_name : "Choose a voice"}
          </span>
          {selected && <span className="t-footnote secondary">{selected.locale_name}</span>}
        </span>
        <Icon.ChevronRight
          width={16}
          height={16}
          strokeWidth={2.2}
          style={{ transform: open ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 200ms" }}
          className="chevron"
        />
      </button>

      {open && (
        <div className="voice-picker__panel">
          <label className="search">
            <Icon.Search width={16} height={16} aria-hidden="true" />
            <input
              ref={searchRef}
              type="search"
              value={query}
              placeholder="Search voices or languages"
              onChange={(event) => setQuery(event.target.value)}
              aria-label="Search voices"
            />
          </label>
          <div className="voice-list" role="listbox">
            {filtered.length === 0 ? (
              <div className="empty" style={{ padding: 24 }}>
                <span className="t-subhead">No voices match “{query}”.</span>
              </div>
            ) : (
              filtered.map((v) => (
                <button
                  key={v.short_name}
                  type="button"
                  role="option"
                  aria-selected={v.short_name === value}
                  className="voice-row"
                  onClick={() => {
                    onChange(v.short_name);
                    setOpen(false);
                    setQuery("");
                  }}
                >
                  <span className="voice-row__avatar" aria-hidden="true">
                    {v.display_name[0]}
                  </span>
                  <span className="voice-row__main">
                    <span className="voice-row__name">{v.display_name}</span>
                    <span className="voice-row__locale">
                      {v.locale_name} · {v.gender}
                    </span>
                  </span>
                  {v.short_name === value && (
                    <Icon.Check width={18} height={18} strokeWidth={2.4} style={{ color: "var(--accent)" }} />
                  )}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
