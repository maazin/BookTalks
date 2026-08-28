import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icons.jsx";
import { api } from "../lib/api.js";

export function Login({ onAuthenticated }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  async function onSubmit(event) {
    event.preventDefault();
    if (!password || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.login(password);
      onAuthenticated();
    } catch (err) {
      setError(err.status === 429 ? err.message : "Wrong password.");
      setPassword("");
      inputRef.current?.focus();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login">
      <form className="card card--pad login__card stack stack-16" onSubmit={onSubmit}>
        <div className="player-hero" style={{ padding: 0 }}>
          <div className="artwork" style={{ width: 72, height: 72, marginBottom: 4 }} aria-hidden="true">
            <Icon.Waveform width={32} height={32} strokeWidth={1.6} />
          </div>
          <h1 className="t-title">BookTalks</h1>
          <p className="t-subhead secondary">Enter the password to open your library.</p>
        </div>

        <div className="stack stack-8">
          <label htmlFor="login-password" className="visually-hidden">
            Password
          </label>
          <input
            ref={inputRef}
            id="login-password"
            type="password"
            inputMode="text"
            autoComplete="current-password"
            className="login__input"
            placeholder="Password"
            value={password}
            disabled={submitting}
            onChange={(event) => {
              setPassword(event.target.value);
              setError(null);
            }}
            aria-invalid={error ? "true" : undefined}
            aria-describedby={error ? "login-error" : undefined}
          />
          {error && (
            <p id="login-error" className="t-footnote" style={{ color: "var(--red)" }} role="alert">
              {error}
            </p>
          )}
        </div>

        <button type="submit" className="btn btn--primary" disabled={!password || submitting}>
          {submitting ? <span className="spinner" /> : "Unlock"}
        </button>
      </form>
    </div>
  );
}
