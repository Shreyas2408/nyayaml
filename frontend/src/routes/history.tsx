import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { getHistory, type Citation, type HistoryItem } from "@/lib/api";
import { ErrorBox, Shell, useAuthGuard } from "@/components/nyaya/Shell";
import { AnswerBody, CitationList, CitationPanel } from "@/components/nyaya/Answer";

export const Route = createFileRoute("/history")({
  head: () => ({
    meta: [
      { title: "Query History — NyayaML" },
      { name: "description", content: "Review your past NyayaML legal research questions." },
      { property: "og:title", content: "Query History — NyayaML" },
      { property: "og:description", content: "Review your past NyayaML legal research questions." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: HistoryPage,
});

const SAVED = "nyaya.saved";

function safeCitations(c: unknown): Citation[] {
  return Array.isArray(c) ? c.filter((x): x is Citation => !!x && typeof x.citation_id === "string" && x.citation_id.length > 0) : [];
}

function HistoryPage() {
  const ready = useAuthGuard();
  const [items, setItems] = useState<HistoryItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<HistoryItem | null>(null);
  const [saved, setSaved] = useState<Record<string, HistoryItem>>({});
  const [cite, setCite] = useState<number | null>(null);
  const [showSaved, setShowSaved] = useState(false);

  useEffect(() => {
    if (!ready) return;
    try { setSaved(JSON.parse(localStorage.getItem(SAVED) ?? "{}")); } catch { /* ignore */ }
    getHistory(50).then(setItems).catch((e) => setErr(e.message));
  }, [ready]);

  const toggleSave = (it: HistoryItem) => {
    const next = { ...saved };
    if (next[it.id]) delete next[it.id]; else next[it.id] = it;
    setSaved(next);
    localStorage.setItem(SAVED, JSON.stringify(next));
  };

  if (!ready) return null;
  const list = showSaved ? Object.values(saved).sort((a, b) => b.created_at.localeCompare(a.created_at)) : items ?? [];
  const cits = open ? safeCitations(open.citations) : [];

  return (
    <Shell>
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-serif text-3xl font-bold">Query History</h1>
          <p className="mt-1 text-sm text-muted-foreground">Your 50 most recent questions. Saved items are kept on this device only.</p>
        </div>
        <div className="flex rounded-md border text-sm">
          <button onClick={() => setShowSaved(false)} className={`px-3 py-1.5 ${!showSaved ? "bg-primary text-primary-foreground" : ""}`}>All</button>
          <button onClick={() => setShowSaved(true)} className={`px-3 py-1.5 ${showSaved ? "bg-primary text-primary-foreground" : ""}`}>Saved ({Object.keys(saved).length})</button>
        </div>
      </div>
      {err && <div className="mt-4"><ErrorBox message={err} /></div>}

      <div className="mt-6 overflow-hidden rounded-lg border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-muted text-left text-xs uppercase tracking-wide text-muted-foreground">
            <tr><th className="px-4 py-2">Question</th><th className="w-48 px-4 py-2">Date</th><th className="w-32 px-4 py-2">Status</th><th className="w-20 px-4 py-2" /></tr>
          </thead>
          <tbody>
            {list.map((it) => (
              <tr key={it.id} onClick={() => { setOpen(it); setCite(null); }} className="cursor-pointer border-t hover:bg-muted">
                <td className="max-w-0 truncate px-4 py-2 font-serif">{it.query_text || <em className="text-muted-foreground">(empty)</em>}</td>
                <td className="px-4 py-2 text-muted-foreground">{new Date(it.created_at).toLocaleString()}</td>
                <td className="px-4 py-2">{it.status === "completed" ? "● Completed" : "○ Failed"}</td>
                <td className="px-4 py-2">
                  <button onClick={(e) => { e.stopPropagation(); toggleSave(it); }} className="text-xs text-muted-foreground hover:text-saffron">
                    {saved[it.id] ? "★ Saved" : "☆ Save"}
                  </button>
                </td>
              </tr>
            ))}
            {list.length === 0 && (
              <tr><td colSpan={4} className="px-4 py-8 text-center text-muted-foreground">{items === null && !showSaved && !err ? "Loading…" : "Nothing here yet."}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {open && (
        <>
          <div className="fixed inset-0 z-30 bg-foreground/20" onClick={() => setOpen(null)} />
          <aside className="fade-in fixed inset-y-0 right-0 z-40 w-full max-w-2xl overflow-y-auto border-l bg-card p-8 shadow-xl">
            <div className="mb-4 flex justify-between">
              <span className="text-xs text-muted-foreground">{new Date(open.created_at).toLocaleString()} · {open.act ?? "All acts"} · {open.status}</span>
              <div className="flex gap-2">
                <button onClick={() => toggleSave(open)} className="rounded-md border px-2 py-1 text-xs">{saved[open.id] ? "★ Saved" : "☆ Save"}</button>
                <button onClick={() => setOpen(null)} className="rounded-md border px-2 py-1 text-xs">✕ Close</button>
              </div>
            </div>
            <p className="mb-6 font-serif text-xl italic">“{open.query_text}”</p>
            {open.answer ? <AnswerBody answer={open.answer} citations={cits} onCite={setCite} /> :
              <p className="text-muted-foreground">{open.error ? `This question failed (${open.error.replace(/_/g, " ")}).` : "No answer was recorded."}</p>}
            <h2 className="mb-2 mt-8 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Sources</h2>
            <CitationList citations={cits} selected={cite} onSelect={setCite} />
          </aside>
          {cite !== null && cits[cite] && <div className="relative z-50"><CitationPanel citation={cits[cite]} index={cite} onClose={() => setCite(null)} /></div>}
        </>
      )}
    </Shell>
  );
}
