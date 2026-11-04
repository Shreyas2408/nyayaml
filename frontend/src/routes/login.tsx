import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { login, register } from "@/lib/api";
import { Disclaimer, ErrorBox } from "@/components/nyaya/Shell";

export const Route = createFileRoute("/login")({
  head: () => ({
    meta: [
      { title: "Sign in — NyayaML" },
      { name: "description", content: "Sign in to NyayaML legal research." },
      { property: "og:title", content: "Sign in — NyayaML" },
      { property: "og:description", content: "Sign in to NyayaML legal research." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: LoginPage,
});

function LoginPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null); setInfo(null);
    if (mode === "register" && (password.length < 12 || new TextEncoder().encode(password).length > 72)) {
      setErr("Password must be 12–72 characters.");
      return;
    }
    setBusy(true);
    try {
      if (mode === "register") {
        await register(email, password);
        setInfo("Account created. Signing you in…");
      }
      await login(email, password);
      navigate({ to: "/" });
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const input = "w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-saffron";
  return (
    <div className="min-h-screen">
      <Disclaimer />
      <div className="mx-auto flex max-w-sm flex-col pt-24">
        <h1 className="text-center font-serif text-3xl font-bold">Nyaya<span className="text-saffron">ML</span></h1>
        <p className="mt-2 text-center text-sm text-muted-foreground">Research Indian criminal law — BNS, BNSS, BSA 2023</p>
        <form onSubmit={submit} className="mt-8 space-y-4 rounded-lg border bg-card p-6 shadow-sm">
          <label className="block text-sm">Email<input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={`${input} mt-1`} /></label>
          <label className="block text-sm">Password<input type="password" required maxLength={72} value={password} onChange={(e) => setPassword(e.target.value)} className={`${input} mt-1`} /></label>
          {mode === "register" && <p className="text-xs text-muted-foreground">12–72 characters.</p>}
          {err && <ErrorBox message={err} />}
          {info && <p className="text-sm text-muted-foreground">{info}</p>}
          <button disabled={busy} className="w-full rounded-md bg-primary py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">
            {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
        <button onClick={() => { setMode(mode === "login" ? "register" : "login"); setErr(null); }} className="mt-4 text-center text-sm text-muted-foreground hover:text-foreground">
          {mode === "login" ? "No account? Register" : "Have an account? Sign in"}
        </button>
      </div>
    </div>
  );
}
