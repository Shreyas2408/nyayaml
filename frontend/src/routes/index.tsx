import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { ACTS, QUOTAS, askQuery, type Act, type QueryResponse } from "@/lib/api";
import { ErrorBox, Shell, useAuthGuard } from "@/components/nyaya/Shell";
import { AnswerBody, CitationList, CitationPanel, ConfidenceBadge } from "@/components/nyaya/Answer";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "NyayaML — Ask about Indian Criminal Law" },
      { name: "description", content: "Research questions on BNS, BNSS and BSA 2023 with cited statutory sources." },
      { property: "og:title", content: "NyayaML — Ask about Indian Criminal Law" },
      { property: "og:description", content: "Cited answers on BNS, BNSS and BSA 2023." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: AskPage,
});

const EXAMPLES = [
  "What is punishment for theft under BNS?",
  "When can police arrest without a warrant under BNSS?",
  "Is electronic evidence admissible under BSA?",
  "What is the definition of murder?",
];

const STAGES: Array<[number, string]> = [
  [0, "Analyzing query intent..."],
  [2, "Searching 1,059 sections..."],
  [10, "Cross-referencing acts..."],
  [30, "Generating answer..."],
  [60, "Verifying citations..."],
];

function ProgressLog({ elapsed }: { elapsed: number }) {
  const shown = STAGES.filter(([t]) => elapsed >= t);
  return (
    <div className="fade-in rounded-md border bg-card p-5 font-mono text-sm" aria-live="polite">
      <div className="mb-3 flex justify-between text-xs text-muted-foreground">
        <span>Estimated progress (not reported by the server)</span>
        <span>Elapsed {elapsed.toFixed(1)}s</span>
      </div>
      {shown.map(([t, label], i) => (
        <p key={t} className={i === shown.length - 1 ? "text-foreground" : "text-muted-foreground"}>
          <span className="text-saffron">{i === shown.length - 1 ? "›" : "✓"}</span> [{String(t).padStart(2, "0")}s] {label}
        </p>
      ))}
      <p className="mt-3 text-xs text-muted-foreground">Answers usually take 70–100 seconds, but may finish earlier or later.</p>
    </div>
  );
}

type Result = { data: QueryResponse; queryId: string | null; receivedAt: string };

function AskPage() {
  const ready = useAuthGuard();
  const [q, setQ] = useState("");
  const [act, setAct] = useState<Act | "">("");
  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [cite, setCite] = useState<number | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => () => { if (timer.current) clearInterval(timer.current); }, []);

  async function submit(text: string) {
    setErr(null); setResult(null); setCite(null); setLoading(true); setElapsed(0);
    const start = performance.now();
    timer.current = window.setInterval(() => setElapsed((performance.now() - start) / 1000), 100);
    try {
      setResult(await askQuery(text, act || null));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      if (timer.current) clearInterval(timer.current);
      setElapsed((performance.now() - start) / 1000);
      setLoading(false);
    }
  }

  if (!ready) return null;
  const d = result?.data;

  return (
    <Shell>
      {!result && !loading && (
        <div className="mx-auto max-w-2xl pt-8 text-center">
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-saffron">BNS · BNSS · BSA 2023</p>
          <h1 className="mt-3 font-serif text-4xl font-bold md:text-5xl">Ask about Indian Criminal Law</h1>
          <p className="mt-4 text-muted-foreground">One question, one cited research note drawn from the statutory text.</p>
        </div>
      )}

      <form
        onSubmit={(e) => { e.preventDefault(); submit(q); }}
        className="mx-auto mt-10 max-w-3xl rounded-lg border bg-card p-3 shadow-sm"
      >
        <textarea
          value={q}
          onChange={(e) => setQ(e.target.value)}
          disabled={loading}
          rows={3}
          placeholder="e.g. What is the punishment for cheating under BNS?"
          className="w-full resize-none bg-transparent p-2 font-serif text-lg outline-none placeholder:text-muted-foreground disabled:opacity-60"
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(q); }}
        />
        <div className="flex flex-wrap items-center gap-3 border-t pt-3">
          <label className="text-xs text-muted-foreground">
            Act{" "}
            <select value={act} onChange={(e) => setAct(e.target.value as Act | "")} disabled={loading} className="ml-1 rounded-md border bg-background px-2 py-1 text-sm text-foreground">
              <option value="">All acts</option>
              {ACTS.map((a) => <option key={a}>{a}</option>)}
            </select>
          </label>
          <span className="text-xs text-muted-foreground">Limit: {QUOTAS.query}</span>
          <button type="submit" disabled={loading} className="ml-auto rounded-md bg-primary px-5 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50">
            {loading ? "Researching…" : "Ask"}
          </button>
        </div>
      </form>

      {!result && !loading && (
        <div className="mx-auto mt-6 flex max-w-3xl flex-wrap justify-center gap-2">
          {EXAMPLES.map((ex) => (
            <button key={ex} onClick={() => { setQ(ex); submit(ex); }} className="rounded-full border px-3 py-1.5 text-sm text-muted-foreground hover:border-saffron hover:text-foreground">
              {ex}
            </button>
          ))}
        </div>
      )}

      <div className="mx-auto mt-8 max-w-5xl">
        {loading && <ProgressLog elapsed={elapsed} />}
        {err && <ErrorBox message={err} />}
        {d && (
          <div className="fade-in grid gap-6 lg:grid-cols-[1fr_280px]">
            <article className="rounded-lg border bg-card p-8 shadow-sm">
              <div className="mb-6 flex flex-wrap items-center gap-3 border-b pb-4">
                <ConfidenceBadge />
                {d.query_type && <span className="rounded-full bg-muted px-2.5 py-0.5 font-mono text-xs">{d.query_type.replace(/_/g, " ")}</span>}
                {d.act && <span className="rounded-full bg-muted px-2.5 py-0.5 font-mono text-xs">{d.act}</span>}
                {d.fallback_used && <span className="rounded-full border px-2.5 py-0.5 text-xs">Fallback answer</span>}
                {!d.citations_valid && <span className="rounded-full border border-destructive/50 px-2.5 py-0.5 text-xs text-destructive">⚠ Citations could not all be verified</span>}
                <span className="ml-auto text-xs text-muted-foreground">
                  Received {new Date(result.receivedAt).toLocaleString()} (your device) · {elapsed.toFixed(1)}s
                </span>
              </div>
              <p className="mb-4 font-serif text-lg italic text-muted-foreground">“{d.query || q}”</p>
              {d.answer ? (
                <AnswerBody answer={d.answer} citations={d.citations} onCite={setCite} />
              ) : (
                <p className="rounded-md border border-dashed p-6 text-center text-muted-foreground">
                  No answer could be produced for this question. Try rephrasing or naming a specific act.
                </p>
              )}
              {d.hallucination_flags.length > 0 && (
                <div className="mt-6 rounded-md border p-3 text-sm">
                  <p className="font-semibold">⚠ Review flags</p>
                  <ul className="ml-5 list-disc text-muted-foreground">{d.hallucination_flags.map((f) => <li key={f}>{f}</li>)}</ul>
                </div>
              )}
              {d.disclaimers.length > 0 && (
                <div className="mt-6 border-t pt-4 text-xs italic text-muted-foreground">
                  {d.disclaimers.map((x) => <p key={x}>{x}</p>)}
                </div>
              )}
              {result.queryId && <p className="mt-2 font-mono text-[10px] text-muted-foreground">Query ID {result.queryId}</p>}
            </article>
            <div>
              <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Sources</h2>
              <CitationList citations={d.citations} selected={cite} onSelect={setCite} />
            </div>
          </div>
        )}
      </div>
      {d && cite !== null && d.citations[cite] && <CitationPanel citation={d.citations[cite]} index={cite} onClose={() => setCite(null)} />}
    </Shell>
  );
}
