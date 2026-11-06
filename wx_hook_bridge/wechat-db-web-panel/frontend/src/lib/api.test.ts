import { afterEach, describe, expect, it, vi } from "vitest";

import { API_BASE, api, assetUrl, fetchApi } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("API helpers", () => {
  it("uses the same-origin API prefix and keeps safe asset URLs", () => {
    expect(API_BASE).toBe("/api");
    expect(assetUrl("/api/avatars/wxid_local")).toBe("/api/avatars/wxid_local");
    expect(assetUrl("data:image/png;base64,AA==")).toBe("data:image/png;base64,AA==");
    expect(assetUrl("avatars/wxid_local")).toBe("/api/avatars/wxid_local");
  });

  it("serializes only defined query parameters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchApi("/overview", { days: 30, empty: "", missing: undefined })).resolves.toEqual({ status: "ok" });
    const requestUrl = fetchMock.mock.calls[0][0] as URL;
    expect(requestUrl.pathname).toBe("/api/overview");
    expect(requestUrl.searchParams.get("days")).toBe("30");
    expect(requestUrl.searchParams.has("empty")).toBe(false);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ headers: { Accept: "application/json" } });
  });

  it("surfaces FastAPI detail messages", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "查询参数无效" }), {
        status: 422,
        statusText: "Unprocessable Entity",
        headers: { "Content-Type": "application/json" },
      }),
    ));

    await expect(fetchApi("/search", { q: "" })).rejects.toMatchObject({ status: 422, message: "查询参数无效" });
  });

  it("sends official-account mutations as same-origin JSON requests", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ read_count: 128 }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ cleared: true, token: { available: false } }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ total: 1, items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ total: 0, items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await api.mpArticleMetrics({ url: "https://mp.weixin.qq.com/s/article", fetch_missing: true });
    await api.mpTokenClear();

    const metricsUrl = fetchMock.mock.calls[0][0] as URL;
    expect(metricsUrl.pathname).toBe("/api/mp/articles/metrics");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ url: "https://mp.weixin.qq.com/s/article", fetch_missing: true }),
    });
    const clearUrl = fetchMock.mock.calls[1][0] as URL;
    expect(clearUrl.pathname).toBe("/api/mp/tokens");
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "DELETE" });

    await api.mpArticleMetricsBatch({ urls: ["https://mp.weixin.qq.com/s/one"] });
    await api.mpArticleHistory({ limit: 8 });
    const batchUrl = fetchMock.mock.calls[2][0] as URL;
    expect(batchUrl.pathname).toBe("/api/mp/articles/metrics/batch");
    expect(fetchMock.mock.calls[2][1]).toMatchObject({ method: "POST" });
    expect(JSON.parse((fetchMock.mock.calls[2][1] as RequestInit).body as string)).toEqual({ urls: ["https://mp.weixin.qq.com/s/one"] });
    const historyUrl = fetchMock.mock.calls[3][0] as URL;
    expect(historyUrl.pathname).toBe("/api/mp/articles/history");
    expect(historyUrl.searchParams.get("limit")).toBe("8");
  });

  it("uses the SNS collection contract for listing and backups", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], total: 0 }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ backup_id: "sns-1", download_url: "/api/sns/backups/sns-1/download" }), { status: 201, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ deleted: true }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await api.sns({ limit: 24, offset: 48, q: "旅行" });
    await api.snsBackup({ include_local_media: true });
    await api.snsDeleteBackup("sns-1");

    const listUrl = fetchMock.mock.calls[0][0] as URL;
    expect(listUrl.pathname).toBe("/api/sns");
    expect(listUrl.searchParams.get("limit")).toBe("24");
    expect(listUrl.searchParams.get("offset")).toBe("48");
    expect(listUrl.searchParams.get("q")).toBe("旅行");
    const createUrl = fetchMock.mock.calls[1][0] as URL;
    expect(createUrl.pathname).toBe("/api/sns/backups");
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "POST", body: JSON.stringify({ include_local_media: true }) });
    const deleteUrl = fetchMock.mock.calls[2][0] as URL;
    expect(deleteUrl.pathname).toBe("/api/sns/backups/sns-1");
    expect(fetchMock.mock.calls[2][1]).toMatchObject({ method: "DELETE" });
  });
});
