import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";
import { STATIC_DISCLAIMER, UNAUTHORIZED_EVENT, logout, tokens } from "@/lib/api";

export function useAuthGuard() {
  const navigate = useNavigate();
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!tokens.access()) {
      navigate({ to: "/login" });
      return;
    }
    setReady(true);
    const on = () => navigate({ to: "/login" });
    window.addEventListener(UNAUTHORIZED_EVENT, on);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, on);
  }, [navigate]);
  return ready;
}

function ThemeToggle() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const d = localStorage.getItem("nyaya.theme") === "dark";
    setDark(d);
    document.documentElement.classList.toggle("dark", d);
  }, []);
  return (
    <button
      onClick={() => {
        const d = !dark;
        setDark(d);
        document.documentElement.classList.toggle("dark", d);
        localStorage.setItem("nyaya.theme", d ? "dark" : "light");
      }}
      className="rounded-md border px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground"
      aria-label="Toggle dark mode"
    >
      {dark ? "☀ Light" : "☾ Dark"}
    </button>
  );
}

export function Disclaimer() {
  return (
    <div role="note" className="border-b border-saffron/40 bg-accent px-4 py-2 text-center text-xs text-accent-foreground">
      <strong className="font-semibold">Disclaimer:</strong> {STATIC_DISCLAIMER}
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const nav = "text-sm text-muted-foreground hover:text-foreground transition-colors";
  const active = { className: "text-sm font-medium text-foreground border-b-2 border-saffron pb-0.5" };
  return (
    <div className="min-h-screen">
      <div className="sticky top-0 z-30 bg-background">
        <Disclaimer />
        <header className="border-b">
          <div className="mx-auto flex h-14 max-w-6xl items-center gap-8 px-6">
            <Link to="/" className="flex items-center gap-2">
              <span className="font-serif text-xl font-bold">Nyaya<span className="text-saffron">ML</span></span>
            </Link>
            <nav className="flex gap-6">
              <Link to="/" className={nav} activeProps={active} activeOptions={{ exact: true }}>Ask</Link>
              <Link to="/sections" search={{ act: "BNS", section: "" }} className={nav} activeProps={active}>Sections</Link>
              <Link to="/history" className={nav} activeProps={active}>History</Link>
            </nav>
            <div className="ml-auto flex items-center gap-3">
              <ThemeToggle />
              <button
                onClick={async () => {
                  await logout();
                  navigate({ to: "/login" });
                }}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                Sign out
              </button>
            </div>
          </div>
        </header>
      </div>
      <main className="mx-auto max-w-6xl px-6 py-10 fade-in">{children}</main>
    </div>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
      {message}
    </div>
  );
}
