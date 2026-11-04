import { afterEach, describe, expect, it, vi } from "vitest";
import { API_BASE, login, parseCitationId } from "./api";

describe("parseCitationId", () => {
  it("extracts act and section from a section ID", () => {
    expect(parseCitationId("BNS-2023-ff92dcc72778-S303")).toEqual({ act: "BNS", section: "303" });
  });
  it("extracts section from a table-row ID", () => {
    expect(parseCitationId("BNSS-2023-8047fe1de609-S359-SS1-T1-R001")).toEqual({ act: "BNSS", section: "359" });
  });
  it("does not treat a schedule ID as a section", () => {
    expect(parseCitationId("BNSS-2023-8047fe1de609-SCH1")).toBeNull();
  });
});

describe("request URLs", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("sends no absolute backend origin, so the dev-server proxy can forward it", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        urls.push(String(input));
        return new Response(
          JSON.stringify({ access_token: "a", refresh_token: "r", token_type: "bearer", expires_in: 900 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }),
    );

    await login("dev@example.com", "hunter2");

    expect(API_BASE).toBe("");
    expect(urls).toEqual(["/api/v1/auth/login"]);
  });
});

