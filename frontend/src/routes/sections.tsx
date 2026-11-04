import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { ACTS, QUOTAS, getActs, type Act, type ActsResponse, type SectionResponse } from "@/lib/api";
import { ErrorBox, Shell, useAuthGuard } from "@/components/nyaya/Shell";
import { SectionLoader } from "@/components/nyaya/SectionText";

export const Route = createFileRoute("/sections")({
  validateSearch: (s: Record<string, unknown>): { act: string; section: string } => ({
    act: typeof s["act"] === "string" ? s["act"] : "BNS",
    section: typeof s["section"] === "string" ? s["section"] : "",
  }),
  head: () => ({
    meta: [
      { title: "Statutory Sections — NyayaML" },
      { name: "description", content: "Read BNS, BNSS and BSA 2023 sections verbatim." },
      { property: "og:title", content: "Statutory Sections — NyayaML" },
      { property: "og:description", content: "Read BNS, BNSS and BSA 2023 sections verbatim." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: SectionsPage,
});

type Seen = { act: Act; number: string; title: string };

function SectionsPage() {
  const ready = useAuthGuard();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const act = (ACTS.includes(search.act as Act) ? search.act : "BNS") as Act;
  const [acts, setActs] = useState<ActsResponse | null>(null);
  const [actsErr, setActsErr] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [seen, setSeen] = useState<Seen[]>([]);

  useEffect(() => {
    if (!ready) return;
    getActs().then(setActs).catch((e) => setActsErr(e.message));
    try { setSeen(JSON.parse(sessionStorage.getItem("nyaya.seen") ?? "[]")); } catch { /* ignore */ }
  }, [ready]);

  const remember = (s: SectionResponse) => {
    setSeen((prev) => {
      const next = [{ act, number: s.section_number, title: s.title }, ...prev.filter((p) => !(p.act === act && p.number === s.section_number))].slice(0, 200);
      sessionStorage.setItem("nyaya.seen", JSON.stringify(next));
      return next;
    });
  };

  const rows = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return seen.filter((s) => s["act"] === act).filter((s) => !f || s.number.toLowerCase().includes(f) || s.title.toLowerCase().includes(f))
      .sort((a, b) => parseInt(a.number) - parseInt(b.number) || a.number.localeCompare(b.number));
  }, [seen, act, filter]);

  const isNumber = /^\d+[A-Za-z]?$/.test(filter.trim());

  if (!ready) return null;
  return (
    <Shell>
      <h1 className="font-serif text-3xl font-bold">Statutory Sections</h1>
      <p className="mt-1 text-sm text-muted-foreground">Verbatim text from the 2023 snapshot. Limit: {QUOTAS.sections}.</p>
      {actsErr && <div className="mt-4"><ErrorBox message={actsErr} /></div>}

      <div role="tablist" className="mt-6 flex gap-1 border-b">
        {ACTS.map((a) => {
          const count = acts?.acts.find((x) => x.act === a)?.section_count;
          return (
            <button key={a} role="tab" aria-selected={a === act}
              onClick={() => navigate({ search: { act: a, section: "" } })}
              className={`-mb-px border-b-2 px-4 py-2 text-sm ${a === act ? "border-saffron font-semibold" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
              {a} <span className="font-mono text-xs text-muted-foreground">({count ?? "—"})</span>
            </button>
          );
        })}
      </div>

      <form className="mt-4 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (isNumber) navigate({ search: { act, section: filter.trim().toUpperCase() } }); }}>
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={`Filter, or enter a ${act} section number (e.g. 303)`}
          className="w-full max-w-md rounded-md border bg-card px-3 py-2 text-sm outline-none focus:border-saffron" />
        <button disabled={!isNumber} className="rounded-md bg-primary px-4 text-sm text-primary-foreground disabled:opacity-40">Open section</button>
      </form>

      <div className="mt-4 overflow-hidden rounded-lg border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-muted text-left text-xs uppercase tracking-wide text-muted-foreground">
            <tr><th className="w-28 px-4 py-2">Section</th><th className="px-4 py-2">Title</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.number} onClick={() => navigate({ search: { act, section: r.number } })} className="cursor-pointer border-t hover:bg-muted">
                <td className="px-4 py-2 font-mono">{r.number}</td>
                <td className="px-4 py-2 font-serif">{r.title}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={2} className="px-4 py-8 text-center text-muted-foreground">
                A full section index is not available from the server yet. Enter a section number above to open it — sections you open appear here.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {search.section && (
        <>
          <div className="fixed inset-0 z-30 bg-foreground/20" onClick={() => navigate({ search: { act, section: "" } })} />
          <aside className="fade-in fixed inset-y-0 right-0 z-40 w-full max-w-xl overflow-y-auto border-l bg-card p-6 shadow-xl">
            <button onClick={() => navigate({ search: { act, section: "" } })} className="mb-4 rounded-md border px-2 py-1 text-xs">✕ Close</button>
            <SectionLoader act={act} section={search.section} onLoaded={remember} />
          </aside>
        </>
      )}
    </Shell>
  );
}
