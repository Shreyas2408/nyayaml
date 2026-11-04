import { afterEach, describe, expect, it, vi } from "vitest";
import { backendOrigin, proxyApiRequest } from "./api-proxy";

describe("backendOrigin", () => {
  const original = { ...process.env };

  afterEach(() => {
    process.env = { ...original };
  });

  it("uses NYAYA_API_ORIGIN when set", () => {
    process.env["NYAYA_API_ORIGIN"] = "http://127.0.0.1:9000/";
    expect(backendOrigin()).toBe("http://127.0.0.1:9000");
  });

  it("falls back to the local backend outside production, and forwards nothing in production", () => {
    delete process.env["NYAYA_API_ORIGIN"];
    process.env["NODE_ENV"] = "development";
    expect(backendOrigin()).toBe("http://127.0.0.1:8000");
    process.env["NODE_ENV"] = "production";
    expect(backendOrigin()).toBeNull();
  });

});

describe("proxyApiRequest", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("forwards method, path, query, body and auth header to the backend", async () => {
    const calls: Array<{ url: string; method: string; body: string; auth: string | null }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const raw = init?.body;
        calls.push({
          url: String(input),
          method: init?.method ?? "GET",
          body: raw instanceof ArrayBuffer ? new TextDecoder().decode(raw) : "",
          auth: new Headers(init?.headers).get("Authorization"),
        });
        return new Response(JSON.stringify({ answer: "ok" }), {
          status: 201,
          headers: { "Content-Type": "application/json", "X-Query-ID": "q-42" },
        });
      }),
    );

    const res = await proxyApiRequest(
      new Request("http://localhost:8080/api/v1/query?x=1", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer tok" },
        body: JSON.stringify({ query: "theft" }),
      }),
      "http://127.0.0.1:8000",
    );

    expect(calls).toEqual([
      { url: "http://127.0.0.1:8000/api/v1/query?x=1", method: "POST", body: '{"query":"theft"}', auth: "Bearer tok" },
    ]);
    expect(res.status).toBe(201);
    expect(res.headers.get("X-Query-ID")).toBe("q-42");
    await expect(res.json()).resolves.toEqual({ answer: "ok" });
  });

  it.each([
    new TypeError("fetch failed"),
    new Error("fetch failed", { cause: { code: "ECONNREFUSED" } }),
  ])("returns a readable 502 when the backend is offline (%s)", async (error) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw error;
      }),
    );

    const res = await proxyApiRequest(new Request("http://localhost:8080/api/v1/ping"), "http://127.0.0.1:8000");

    expect(res.status).toBe(502);
    await expect(res.json()).resolves.toEqual({
      detail: "Cannot reach the server. Check that the backend is running.",
    });
  });

  it.each(["TimeoutError", "AbortError"])("distinguishes %s from connection failures", async (name) => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new DOMException("The request timed out", name);
    }));

    const res = await proxyApiRequest(new Request("http://localhost:8080/api/v1/query"), "http://127.0.0.1:8000");

    expect(res.status).toBe(502);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    await expect(res.json()).resolves.toEqual({
      detail: "The server is taking longer than expected. Please try again.",
    });
  });
});
