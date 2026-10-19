import { describe, expect, it } from "vitest";

import { json, mockFetch } from "@/test/utils";

import { api, ApiError, unwrap } from "./client";

describe("unwrap", () => {
  it("returns data", async () => {
    mockFetch({ "GET /api/runs/:id": (_req: Request, params: Record<string, string>) => ({ id: Number(params.id) }) });
    const run = await unwrap(api.GET("/api/runs/{run_id}", { params: { path: { run_id: 7 } } }));
    expect(run.id).toBe(7);
  });

  it("throws an ApiError with FastAPI's detail", async () => {
    mockFetch({ "POST /api/versions/:id/promote": () => json({ detail: "Not promotable: scratch" }, 409) });
    const error = await unwrap(
      api.POST("/api/versions/{version_id}/promote", { params: { path: { version_id: 1 } } }),
    ).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, detail: "Not promotable: scratch", message: "Not promotable: scratch" });
  });

  it("joins validation errors", async () => {
    mockFetch({ "GET /api/runs": () => json({ detail: [{ msg: "bad limit" }, { msg: "bad dag" }] }, 422) });
    await expect(unwrap(api.GET("/api/runs"))).rejects.toThrow("bad limit; bad dag");
  });
});
