import { useEffect, useState } from "react";
import { ApiError, getSection, type Act, type Clause, type SectionResponse, type SubClause } from "@/lib/api";
import { ErrorBox } from "./Shell";

const pre = "whitespace-pre-wrap";

function SubClauses({ items }: { items?: SubClause[] | undefined }) {
  if (!items?.length) return null;
  return (
    <div className="ml-5 space-y-1">
      {items.map((s) => (
        <div key={s.id}>
          {s.intro_text && <p className={pre}>{s.intro_text}</p>}
          {s.text && <p className={pre}>{s.text}</p>}
          <SubClauses items={s.sub_clauses} />
          {s.residual_text && <p className={pre}>{s.residual_text}</p>}
        </div>
      ))}
    </div>
  );
}

function Clauses({ items }: { items?: Clause[] | undefined }) {
  if (!items?.length) return null;
  return (
    <div className="ml-5 space-y-1">
      {items.map((c) => (
        <div key={c.id}>
          {c.intro_text && <p className={pre}>{c.intro_text}</p>}
          {c.text && <p className={pre}>{c.text}</p>}
          <SubClauses items={c.sub_clauses} />
          {c.residual_text && <p className={pre}>{c.residual_text}</p>}
        </div>
      ))}
    </div>
  );
}

export function SectionBody({ act, s }: { act: Act; s: SectionResponse }) {
  return (
    <article className="space-y-4">
      <header>
        <p className="font-mono text-xs uppercase tracking-wide text-muted-foreground">
          {act} 2023 · {s.part ? `Part ${s.part.number} · ` : ""}Chapter {s.chapter?.number} — {s.chapter?.title}
        </p>
        {s.group_heading && <p className="mt-1 text-xs italic text-muted-foreground">{s.group_heading}</p>}
        <h3 className="mt-2 font-serif text-xl font-bold">
          Section {s.section_number}. {s.title}
        </h3>
      </header>
      <div className="space-y-2 font-serif text-[15px] leading-relaxed">
        {s.text && <p className={pre}>{s.text}</p>}
        <Clauses items={s.clauses} />
        {s.subsections?.map((ss) => (
          <div key={ss.id} className="space-y-1">
            {ss.intro_text && <p className={pre}>{ss.intro_text}</p>}
            {ss.text && <p className={pre}>{ss.text}</p>}
            <Clauses items={ss.clauses} />
            {ss.residual_text && <p className={pre}>{ss.residual_text}</p>}
            {ss.tables?.map((t, i) => (
              <div key={i} className="mt-3 overflow-x-auto">
                <p className="mb-1 font-sans text-xs font-semibold">{t.title}</p>
                <table className="w-full border font-sans text-xs">
                  <thead className="bg-muted">
                    <tr>{t.columns.map((c) => <th key={c} className="border px-2 py-1 text-left">{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    {t.rows.map((r) => (
                      <tr key={r.row_id}>
                        <td className="border px-2 py-1">{r.offence}</td>
                        <td className="border px-2 py-1">{r.bns_section}</td>
                        <td className="border px-2 py-1">{r.compoundable_by}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        ))}
        {s.residual_text && <p className={pre}>{s.residual_text}</p>}
      </div>
      {!!s.footnotes?.length && (
        <div className="border-t pt-3 text-xs text-muted-foreground">
          {s.footnotes.map((f) => <p key={f.id} className={pre}>{f.id}. {f.text}</p>)}
        </div>
      )}
      {Array.isArray(s.related_sections) && s.related_sections.length > 0 && (
        <div className="border-t pt-3 text-sm">
          <p className="font-semibold">Related sections</p>
          <p className="text-muted-foreground">{s.related_sections.map(String).join(", ")}</p>
        </div>
      )}
      <p className="border-t pt-3 text-xs italic text-muted-foreground">{s.disclaimer}</p>
    </article>
  );
}

export function SectionLoader({ act, section, onLoaded }: { act: Act; section: string; onLoaded?: ((s: SectionResponse) => void) | undefined }) {
  const [state, setState] = useState<{ s?: SectionResponse; err?: string }>({});
  useEffect(() => {
    let live = true;
    setState({});
    getSection(act, section)
      .then((s) => {
        if (!live) return;
        setState({ s });
        onLoaded?.(s);
      })
      .catch((e: ApiError) => live && setState({ err: e.status === 404 ? `Section ${section} was not found in ${act}.` : e.message }));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [act, section]);
  if (state.err) return <ErrorBox message={state.err} />;
  if (!state.s) return <p className="font-mono text-xs text-muted-foreground">Loading section text…</p>;
  return <SectionBody act={act} s={state.s} />;
}
