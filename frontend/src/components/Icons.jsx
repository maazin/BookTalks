/* Line icons drawn on a 24pt grid with a consistent 1.8 stroke weight, so they
   sit at the same optical weight as the surrounding semibold text. */
const base = {
  width: 24,
  height: 24,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": "true",
  focusable: "false",
};

export const Icon = {
  Waveform: (p) => (
    <svg {...base} {...p}>
      <path d="M3 12h2M8 6v12M12 3v18M16 7.5v9M20 11v2" />
    </svg>
  ),
  Doc: (p) => (
    <svg {...base} {...p}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5M9 13h6M9 17h4" />
    </svg>
  ),
  UploadDoc: (p) => (
    <svg {...base} {...p}>
      <path d="M12 16V4m0 0L8 8m4-4 4 4" />
      <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
    </svg>
  ),
  Play: (p) => (
    <svg {...base} fill="currentColor" stroke="none" {...p}>
      <path d="M8 5.14v13.72c0 .83.92 1.33 1.62.88l10.1-6.86a1.06 1.06 0 0 0 0-1.76L9.62 4.26A1.05 1.05 0 0 0 8 5.14z" />
    </svg>
  ),
  Pause: (p) => (
    <svg {...base} fill="currentColor" stroke="none" {...p}>
      <rect x="6" y="4.5" width="4.2" height="15" rx="1.6" />
      <rect x="13.8" y="4.5" width="4.2" height="15" rx="1.6" />
    </svg>
  ),
  Back15: (p) => (
    <svg {...base} {...p}>
      <path d="M11.5 4.5 7 8l4.5 3.5" />
      <path d="M7 8h5.5a7 7 0 1 1-7 7" />
      <text x="12" y="18.4" textAnchor="middle" fontSize="7.2" fontWeight="700"
        fill="currentColor" stroke="none" fontFamily="inherit">15</text>
    </svg>
  ),
  Forward15: (p) => (
    <svg {...base} {...p}>
      <path d="M12.5 4.5 17 8l-4.5 3.5" />
      <path d="M17 8h-5.5a7 7 0 1 0 7 7" />
      <text x="12" y="18.4" textAnchor="middle" fontSize="7.2" fontWeight="700"
        fill="currentColor" stroke="none" fontFamily="inherit">15</text>
    </svg>
  ),
  ChevronRight: (p) => (
    <svg {...base} {...p}>
      <path d="m9.5 5.5 6.5 6.5-6.5 6.5" />
    </svg>
  ),
  ChevronLeft: (p) => (
    <svg {...base} {...p}>
      <path d="M14.5 5.5 8 12l6.5 6.5" />
    </svg>
  ),
  Trash: (p) => (
    <svg {...base} {...p}>
      <path d="M4 7h16M10 4h4M9.5 11v6M14.5 11v6" />
      <path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" />
    </svg>
  ),
  Search: (p) => (
    <svg {...base} {...p}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </svg>
  ),
  Warning: (p) => (
    <svg {...base} {...p}>
      <path d="M12 4.5 2.8 20h18.4L12 4.5z" />
      <path d="M12 10v4.2M12 17.3v.2" />
    </svg>
  ),
  Check: (p) => (
    <svg {...base} {...p}>
      <path d="m5 12.5 4.5 4.5L19 7" />
    </svg>
  ),
  Clock: (p) => (
    <svg {...base} {...p}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </svg>
  ),
  Volume: (p) => (
    <svg {...base} {...p}>
      <path d="M4 10v4h3.5L12 17.5v-11L7.5 10H4z" strokeLinejoin="round" />
      <path d="M15.5 9a4.5 4.5 0 0 1 0 6M18 6.5a8 8 0 0 1 0 11" />
    </svg>
  ),
  VolumeMuted: (p) => (
    <svg {...base} {...p}>
      <path d="M4 10v4h3.5L12 17.5v-11L7.5 10H4z" strokeLinejoin="round" />
      <path d="m15.5 10.5 4 4M19.5 10.5l-4 4" />
    </svg>
  ),
  Logout: (p) => (
    <svg {...base} {...p}>
      <path d="M15 4H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8" />
      <path d="M10 12h11m0 0-3.5-3.5M21 12l-3.5 3.5" />
    </svg>
  ),
  Pages: (p) => (
    <svg {...base} {...p}>
      <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H10a2 2 0 0 1 2 2v13a2 2 0 0 0-2-2H5.5A1.5 1.5 0 0 1 4 15.5z" />
      <path d="M20 5.5A1.5 1.5 0 0 0 18.5 4H14a2 2 0 0 0-2 2v13a2 2 0 0 1 2-2h4.5a1.5 1.5 0 0 0 1.5-1.5z" />
    </svg>
  ),
};
