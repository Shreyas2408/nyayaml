import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useState } from "react";
import { parseCitationId, type Citation } from "@/lib/api";
import { SectionLoader } from "./SectionText";

export function ConfidenceBadge() {
  // The API does not return a confidence value; never derive one.
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs text-muted-foreground" title="The server does not report a confidence score">
      <span aria-hidden className="font-mono">◌</span> Confidence unavailable
    </span>
  );
}

function linkMarkers(md: string, n: number) {
  return md.replace(/\[(\d{1,2})\](?!\()/g, (m, d) => {
    const i = parseInt(d, 10);
    return i >= 1 && i <= n ? `[\\[${i}\\]](#cite-${i})` : m;
  });
}

export function AnswerBody({ answer, citations, onCite }: { answer: string; citations: Citation[]; onCite: (i: number) => void }) {
  return (
    <div className="legal-prose text-[16px]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            const m = href?.match(/^#cite-(\d+)$/);
            if (m)
              return (
                <button
                  onClick={() => onCite(parseInt(m[1] ?? "1", 10) - 1)}
                  className="mx-0.5 rounded-sm bg-accent px-1 font-sans text-xs font-semibold text-accent-foreground hover:bg-saffron hover:text-saffron-foreground"
                >
                  {children}
                </button>
              );
            return <a href={href} className="underline">{children}</a>;
          },
        }}
      >
        {linkMarkers(answer, citations.length)}
      </ReactMarkdown>
    </div>
  );
}

export function CitationList({ citations, selected, onSelect }: { citations: Citation[]; selected: number | null; onSelect: (i: number) => void }) {
  if (!citations.length) return <p className="text-sm text-muted-foreground">No citations were returned.</p>;
  return (
    <ol className="space-y-2">
      {citations.map((c, i) => (
        <li key={c.citation_id + i}>
          <button
            onClick={() => onSelect(i)}
            className={`w-full rounded-md border px-3 py-2 text-left text-sm transition-colors ${selected === i ? "border-saffron bg-accent" : "hover:bg-muted"}`}
          >
            <span className="font-mono text-xs font-semibold">[{i + 1}]</span>{" "}
            <span className="font-medium">{c.header ?? c.act ?? "Citation"}</span>
            <span className="mt-0.5 block truncate font-mono text-[10px] text-muted-foreground">{c.citation_id}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

export function CitationPanel({ citation, index, onClose }: { citation: Citation; index: number; onClose: () => void }) {
  const ref = parseCitationId(citation.citation_id);
  return (
    <aside className="fade-in fixed inset-y-0 right-0 z-40 w-full max-w-xl overflow-y-auto border-l bg-card p-6 shadow-xl">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <p className="font-mono text-xs text-muted-foreground">Citation [{index + 1}]</p>
          <p className="font-semibold">{citation.header ?? "Source"}</p>
          <p className="font-mono text-[10px] text-muted-foreground break-all">{citation.citation_id}</p>
        </div>
        <button onClick={onClose} className="rounded-md border px-2 py-1 text-xs" aria-label="Close citation panel">✕ Close</button>
      </div>
      {ref ? (
        <SectionLoader act={ref.act} section={ref.section} />
      ) : (
        <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          Source text is unavailable for this citation (it is not a recognised section reference).
        </p>
      )}
    </aside>
  );
}

export function useCitationPanel() {
  return useState<number | null>(null);
}
