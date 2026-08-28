import { useCallback, useEffect, useState } from "react";
import { Library } from "./components/Library.jsx";
import { Player } from "./components/Player.jsx";
import { Login } from "./components/Login.jsx";
import { Icon } from "./components/Icons.jsx";
import { api, onSessionExpired } from "./lib/api.js";

/** Minimal path router: "/" is the library, "/d/:id" is a player. */
function routeFromPath(pathname) {
  const match = pathname.match(/^\/d\/(\d+)$/);
  return match ? { view: "player", id: Number(match[1]) } : { view: "library" };
}

export default function App() {
  const [route, setRoute] = useState(() => routeFromPath(window.location.pathname));
  const [toast, setToast] = useState(null);
  const [scrolled, setScrolled] = useState(false);

  // null while checking; then { required, authenticated }. Most deployments
  // (anything without BOOKTALKS_PASSWORD set) never show a login screen at
  // all — required stays false forever, same as before this existed.
  const [auth, setAuth] = useState(null);

  const checkAuth = useCallback(() => {
    api
      .authStatus()
      .then(setAuth)
      // The API being briefly unreachable shouldn't strand people on a blank
      // screen — assume no gate and let normal request-level error handling
      // (Library/Player's own retry-on-failure) take it from there.
      .catch(() => setAuth({ required: false, authenticated: true }));
  }, []);

  useEffect(checkAuth, [checkAuth]);

  useEffect(() => {
    onSessionExpired(() => setAuth({ required: true, authenticated: false }));
    return () => onSessionExpired(null);
  }, []);

  useEffect(() => {
    const onPop = () => setRoute(routeFromPath(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 4);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const navigate = useCallback((path) => {
    window.history.pushState({}, "", path);
    setRoute(routeFromPath(path));
    window.scrollTo({ top: 0 });
  }, []);

  const showToast = useCallback((message) => {
    setToast({ message, key: Date.now() });
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 3600);
    return () => clearTimeout(timer);
  }, [toast]);

  // Checking with the server before showing anything avoids a flash of the
  // library that a locked deployment would immediately hide again.
  if (auth === null) {
    return <div className="app" />;
  }

  if (auth.required && !auth.authenticated) {
    return (
      <div className="app">
        <Login onAuthenticated={() => setAuth({ required: true, authenticated: true })} />
      </div>
    );
  }

  return (
    <div className="app">
      {/* Navigation-bar convention: the leading slot holds the app name at the
          top level, and a back control once you're one level down. */}
      <header className="toolbar" data-scrolled={scrolled}>
        {route.view === "player" ? (
          <button type="button" className="btn btn--plain" onClick={() => navigate("/")}>
            <Icon.ChevronLeft width={18} height={18} strokeWidth={2.4} aria-hidden="true" />
            Library
          </button>
        ) : (
          <span className="toolbar__title">
            <Icon.Waveform width={20} height={20} strokeWidth={2} aria-hidden="true" />
            BookTalks
          </span>
        )}
        <span className="toolbar__spacer" />
        {auth.required && (
          <button
            type="button"
            className="btn btn--icon btn--plain"
            onClick={() => {
              api.logout().finally(() => setAuth({ required: true, authenticated: false }));
            }}
            aria-label="Sign out"
            title="Sign out"
          >
            <Icon.Logout width={19} height={19} strokeWidth={1.9} />
          </button>
        )}
      </header>

      <main className="container">
        {route.view === "player" ? (
          <Player
            key={route.id}
            documentId={route.id}
            onToast={showToast}
            onExit={() => navigate("/")}
          />
        ) : (
          <Library onOpen={(id) => navigate(`/d/${id}`)} onToast={showToast} />
        )}
      </main>

      <div aria-live="polite" role="status">
        {toast && <div className="toast">{toast.message}</div>}
      </div>
    </div>
  );
}
