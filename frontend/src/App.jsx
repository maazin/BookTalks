import { useCallback, useEffect, useState } from "react";
import { Library } from "./components/Library.jsx";
import { Player } from "./components/Player.jsx";
import { Icon } from "./components/Icons.jsx";

/** Minimal path router: "/" is the library, "/d/:id" is a player. */
function routeFromPath(pathname) {
  const match = pathname.match(/^\/d\/(\d+)$/);
  return match ? { view: "player", id: Number(match[1]) } : { view: "library" };
}

export default function App() {
  const [route, setRoute] = useState(() => routeFromPath(window.location.pathname));
  const [toast, setToast] = useState(null);
  const [scrolled, setScrolled] = useState(false);

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
