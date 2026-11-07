import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("publication reservation problems", () => {
  it("preserves a safe task identity for the shared task detail link", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: "realtime_task_reserved", detail: "realtime_task_reserved", task_uuid: "situation-run-1",
      href: "https://untrusted.example/", request_id: "request-1",
    }), { status: 409, headers: { "content-type": "application/problem+json" } })));
    const error = await apiFetch("/publish/realtime/surveillance/retry", { method: "POST", body: "{}" })
      .catch((value: unknown) => value);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: "realtime_task_reserved", taskUuid: "situation-run-1", requestId: "request-1" });
    expect(error).not.toHaveProperty("href");
  });

  it.each(["../../admin", "<script>", "x".repeat(161), 7, null])("ignores an invalid task identity %s", async taskUuid => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: "realtime_task_reserved", detail: "Reserved", task_uuid: taskUuid,
    }), { status: 409, headers: { "content-type": "application/problem+json" } })));
    const error = await apiFetch("/publish/realtime/surveillance/retry").catch((value: unknown) => value);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).taskUuid).toBeUndefined();
  });
});
