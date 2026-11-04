// Browser-only API client for the NyayaML FastAPI backend.
//
// Paths are same-origin by default ("/api/v1/..."): the dev server proxies
// /api/* to the FastAPI backend (see the proxy in vite.config.ts), so the
// browser never sends a cross-origin request and the backend's missing CORS
// middleware never blocks us. Set VITE_NYAYA_API_BASE to an absolute origin
// only when you want to skip the proxy and talk to the backend directly.
export const API_BASE: string =
  (import.meta.env["VITE_NYAYA_API_BASE"] as string | undefined)?.replace(/\/+$/, "") ?? "";


export type Act = "BNS" | "BNSS" | "BSA";
export const ACTS: Act[] = ["BNS", "BNSS", "BSA"];

export type Citation = {
  citation_id: string;
  act: Act | null;
  header: string | null;
  chunk_id: string | null;
};

export type QueryResponse = {
  query: string;
  act: Act | null;
  top_k: number;
  answer: string | null;
  query_type: string | null;
  citations: Citation[];
  citations_valid: boolean;
  hallucination_flags: string[];
  retrieved_citation_ids: string[];
  fallback_used: boolean;
  disclaimers: string[];
  error: string | null;
};

export type HistoryItem = {
  id: string;
  query_text: string;
  act: string | null;
  status: "completed" | "failed";
  created_at: string;
  answer: string | null;
  citations: Citation[] | null;
  error: string | null;
};

export type ActsResponse = {
  acts: Array<{ act: Act; section_count: number }>;
  source_snapshot: "2023";
  disclaimer: string;
};

export type Heading = { number: string; title: string };
export type SubClause = { id: string; text: string; residual_text: string; intro_text?: string; sub_clauses?: SubClause[] };
export type Clause = { id: string; text: string; intro_text: string; sub_clauses: SubClause[]; residual_text: string };
export type CorpusTable = {
  title: string;
  columns: string[];
  rows: Array<{ row_id: string; offence: string; bns_section: string; compoundable_by: string }>;
};
export type Subsection = { id: string; text: string; intro_text: string; clauses: Clause[]; residual_text: string; tables?: CorpusTable[] };
export type SectionResponse = {
  section_number: string;
  title: string;
  chapter: Heading;
  text: string;
  subsections: Subsection[];
  residual_text: string;
  clauses?: Clause[];
  group_heading?: string;
  part?: Heading;
  footnotes?: Array<{ id: string; text: string }>;
  related_sections?: unknown;
  source_snapshot: "2023";
  disclaimer: string;
};

export const QUOTAS = {
  query: "30 questions per hour",
  sections: "120 section lookups per hour",
  analytics: "120 requests per hour",
};

export class ApiError extends Error {
  status: number;
  retryAfter: number | null;
  constructor(status: number, message: string, retryAfter: number | null = null) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

// ---------- token storage ----------
const ACCESS = "nyaya.access_token";
const REFRESH = "nyaya.refresh_token";

export const tokens = {
  access: () => (typeof window === "undefined" ? null : localStorage.getItem(ACCESS)),
  refresh: () => (typeof window === "undefined" ? null : localStorage.getItem(REFRESH)),
  set(access: string, refresh: string) {
    localStorage.setItem(ACCESS, access);
    localStorage.setItem(REFRESH, refresh);
  },
  clear() {
    localStorage.removeItem(ACCESS);
    localStorage.removeItem(REFRESH);
  },
};

// ---------- error decoding ----------
async function toApiError(res: Response): Promise<ApiError> {
  const ra = res.headers.get("Retry-After");
  const retryAfter = ra && /^\d+$/.test(ra) ? parseInt(ra, 10) : null;
  let detail: unknown = null;
  try {
    detail = (await res.json())?.detail;
  } catch {
    /* non-JSON */
  }
  let msg: string;
  if (typeof detail === "string") msg = detail;
  else if (Array.isArray(detail))
    msg = detail.map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".") || "field"}: ${d.msg}`).join("; ");
  else msg = `Request failed (HTTP ${res.status})`;
  if (res.status === 429) msg = retryAfter != null ? `Rate limit exceeded. Try again in ${formatWait(retryAfter)}.` : "Rate limit exceeded. Please wait before trying again.";
  if (res.status === 500) msg = detail === "Query processing failed" ? "The answer could not be generated. Please try again." : msg;
  if (res.status === 503) msg = `${msg}. This is temporary — please retry shortly.`;
  return new ApiError(res.status, msg, retryAfter);
}

function formatWait(s: number) {
  if (s < 60) return `${s}s`;
  return `${Math.ceil(s / 60)} min`;
}

async function safeFetch(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch {
    throw new ApiError(
      0,
      API_BASE
        ? `Cannot reach the NyayaML server at ${API_BASE}. It may be offline, or it may be blocking browser requests (CORS is not configured on the backend).`
        : "Cannot reach the NyayaML server through this app's /api proxy. The backend may be offline, or the proxy target in vite.config.ts may point at the wrong address.",
    );
  }
}


// ---------- auth ----------
type TokenPair = { access_token: string; refresh_token: string; token_type: "bearer"; expires_in: number };

async function postJson(path: string, body: unknown, headers: Record<string, string> = {}) {
  return safeFetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

export async function register(email: string, password: string): Promise<{ id: string; email: string }> {
  const res = await postJson("/api/v1/auth/register", { email, password });
  if (!res.ok) throw await toApiError(res);
  return res.json();
}

export async function login(email: string, password: string): Promise<void> {
  const res = await postJson("/api/v1/auth/login", { email, password });
  if (!res.ok) throw await toApiError(res);
  const t: TokenPair = await res.json();
  tokens.set(t.access_token, t.refresh_token);
}

let refreshing: Promise<boolean> | null = null;
function refreshTokens(): Promise<boolean> {
  // Refresh tokens are single-use: serialize attempts.
  if (refreshing) return refreshing;
  refreshing = (async () => {
    const rt = tokens.refresh();
    if (!rt) return false;
    try {
      const res = await postJson("/api/v1/auth/refresh", { refresh_token: rt });
      if (!res.ok) {
        tokens.clear();
        return false;
      }
      const t: TokenPair = await res.json();
      tokens.set(t.access_token, t.refresh_token);
      return true;
    } catch {
      return false;
    }
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

export async function logout(): Promise<void> {
  const rt = tokens.refresh();
  tokens.clear();
  if (!rt) return;
  try {
    await postJson("/api/v1/auth/logout", { refresh_token: rt }); // 204, no body
  } catch {
    /* session already cleared locally */
  }
}

export const UNAUTHORIZED_EVENT = "nyaya:unauthorized";

async function authed(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const at = tokens.access();
  if (!at) {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    throw new ApiError(401, "Please sign in.");
  }
  const res = await safeFetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), Authorization: `Bearer ${at}` },
  });
  if (res.status === 401) {
    if (retry && (await refreshTokens())) return authed(path, init, false);
    tokens.clear();
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    throw new ApiError(401, "Your session has expired. Please sign in again.");
  }
  if (!res.ok) throw await toApiError(res);
  return res;
}

// ---------- endpoints ----------
export async function askQuery(query: string, act: Act | null, top_k = 3) {
  const res = await authed("/api/v1/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, act, top_k }),
  });
  const data: QueryResponse = await res.json();
  return { data, queryId: res.headers.get("X-Query-ID"), receivedAt: new Date().toISOString() };
}

export async function getHistory(limit = 50): Promise<HistoryItem[]> {
  const res = await authed(`/api/v1/query/history?limit=${Math.min(100, Math.max(1, limit))}`);
  return res.json();
}

export async function getActs(): Promise<ActsResponse> {
  return (await authed("/api/v1/sections/acts")).json();
}

export async function getSection(act: Act, section: string): Promise<SectionResponse> {
  return (await authed(`/api/v1/sections/${encodeURIComponent(act)}/${encodeURIComponent(section.trim())}`)).json();
}

// ---------- citation adapter ----------
// Only recognised section-based IDs, e.g. BNS-2023-ff92dcc72778-S303 or
// BNSS-2023-8047fe1de609-S359-SS1-T1-R001. Schedules/forms/unknown → null.
export function parseCitationId(id: string): { act: Act; section: string } | null {
  const m = /^(BNS|BNSS|BSA)-2023-[0-9a-f]+-S(\d+[A-Z]?)(?:-|$)/.exec(id);
  return m && m[2] ? { act: m[1] as Act, section: m[2] } : null;
}

export const STATIC_DISCLAIMER =
  "This is a 2023 statutory snapshot, not current legal advice. Verify with official sources.";
