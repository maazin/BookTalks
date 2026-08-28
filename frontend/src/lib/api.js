const BASE = "/api";

const UNREACHABLE = "Can't reach the BookTalks server — is it running?";
const REQUEST_TIMEOUT_MS = 15000;

// Fired when a request comes back 401 outside of the login form itself — that
// means a session expired mid-use, not that a password was typed wrong. App
// listens for this to drop back to the login screen instead of showing every
// screen its own "please sign in" error.
let unauthorizedListener = null;
export function onSessionExpired(listener) {
  unauthorizedListener = listener;
}

async function request(path, options = {}) {
  let response;
  try {
    // Without a deadline, a proxy that accepts the connection but never
    // answers would leave the UI spinning indefinitely.
    response = await fetch(`${BASE}${path}`, {
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      // Same-origin by default; explicit for the split-deployment case where
      // the frontend and API are on different origins (CORS + credentials).
      credentials: "include",
      ...options,
    });
  } catch {
    const error = new Error(UNREACHABLE);
    error.status = 0;
    throw error;
  }

  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/login" && unauthorizedListener) {
      unauthorizedListener();
    }
    // A gateway error means the backend is down, not that the request was bad.
    let detail = response.status >= 500 ? UNREACHABLE : `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body && body.detail) detail = body.detail;
    } catch {
      /* response wasn't JSON — keep the message above */
    }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

export const api = {
  authStatus: () => request("/auth/status"),
  login: (password) =>
    request("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    }),
  logout: () => request("/auth/logout", { method: "POST" }),
  listDocuments: () => request("/documents"),
  getDocument: (id) => request(`/documents/${id}`),
  getPages: (id) => request(`/documents/${id}/pages`),
  getPlayback: (id) => request(`/documents/${id}/playback`),
  savePlayback: (id, positionSec, playbackRate, keepalive = false) =>
    request(`/documents/${id}/playback`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ position_sec: positionSec, playback_rate: playbackRate }),
      // keepalive lets the last save survive the tab closing.
      keepalive,
    }),
  deleteDocument: (id) => request(`/documents/${id}`, { method: "DELETE" }),
  audioUrl: (id) => `${BASE}/documents/${id}/audio`,
  upload(file, onProgress) {
    // XHR rather than fetch: upload progress matters for a 40 MB textbook.
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const form = new FormData();
      form.append("file", file);
      xhr.open("POST", `${BASE}/documents`);
      xhr.withCredentials = true;
      xhr.upload.onprogress = (event) => {
        if (onProgress && event.lengthComputable) {
          onProgress(event.loaded / event.total);
        }
      };
      xhr.onload = () => {
        let payload = null;
        try {
          payload = JSON.parse(xhr.responseText);
        } catch {
          payload = null;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(payload);
          return;
        }
        if (xhr.status === 401 && unauthorizedListener) unauthorizedListener();
        const error = new Error((payload && payload.detail) || "Upload failed.");
        error.status = xhr.status;
        reject(error);
      };
      xhr.onerror = () => reject(new Error(UNREACHABLE));
      xhr.send(form);
    });
  },
};
