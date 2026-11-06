import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../lib/api";
import { MpWorkbenchPage } from "./MpWorkbenchPage";

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><MpWorkbenchPage /></QueryClientProvider>);
}

describe("MpWorkbenchPage", () => {
  beforeEach(() => {
    vi.spyOn(api, "mpTokenStatus").mockResolvedValue({
      token: {
        available: true,
        complete: true,
        expired: false,
        fields: {
          key: { present: true, value: "key1...key9" },
          uin: { present: true, value: "1234...7890" },
          pass_ticket: { present: true, value: "pass...cket" },
        },
        auxiliary: { appmsg_token: { present: true, value: "appm...oken" } },
        captured_at: "2026-08-18T12:00:00+08:00",
        expires_at: "2026-08-18T14:00:00+08:00",
        source: "native-browser-event",
      },
      sources: {
        native_article_context_available: true,
        native_article_context_count: 1,
        native_article_context_captured_at: "2026-08-18T12:00:30+08:00",
      },
    });
    vi.spyOn(api, "mpArticleMetrics").mockResolvedValue({
      url: "https://mp.weixin.qq.com/s/article",
      title: "一篇真实文章",
      author: "测试公众号",
      publish_time: 1_755_475_200,
      read_count: 128_764,
      wow_count: 431,
      like_count: 982,
      comment_count: 27,
      comments: [{ content_id: "c1", nickname: "读者甲", content: "内容很有价值", like_count: 6 }],
      sources: ["live_getappmsgext", "live_comments"],
      metric_basis: { read_count: "real_read_num" },
      collected_at: "2026-08-18T12:01:00+08:00",
    });
    vi.spyOn(api, "mpTokenIngest").mockResolvedValue({
      captured: ["key", "uin", "pass_ticket", "appmsg_token"],
      changed: ["key", "uin", "pass_ticket", "appmsg_token"],
      source: "manual-import",
      token: {
        available: true,
        complete: true,
        expired: false,
        fields: {
          key: { present: true, value: "new-...-key" },
          uin: { present: true, value: "****" },
          pass_ticket: { present: true, value: "new-...cket" },
        },
        auxiliary: { appmsg_token: { present: true, value: "new-...oken" } },
      },
    });
    vi.spyOn(api, "mpTokenClear").mockResolvedValue({
      cleared: true,
      token: {
        available: false,
        complete: false,
        expired: false,
        fields: {
          key: { present: false },
          uin: { present: false },
          pass_ticket: { present: false },
        },
      },
    });
    vi.spyOn(api, "mpExport").mockResolvedValue({
      export_id: "backup-1",
      title: "一篇真实文章",
      archive: "backup.zip",
      download_url: "/api/mp/exports/backup-1/backup.zip",
      files: {
        markdown: "/api/mp/exports/backup-1/article.md",
        pdf: "/api/mp/exports/backup-1/article.pdf",
        manifest: "/api/mp/exports/backup-1/manifest.json",
      },
      resources: [],
      warnings: [],
      pdf_backend: "playwright",
    });
    vi.spyOn(api, "mpArticleHistory").mockResolvedValue({
      items: [{
        url: "https://mp.weixin.qq.com/s/history",
        title: "历史快照文章",
        read_count: 900,
        wow_count: 31,
        like_count: 12,
        comment_count: 4,
        collected_at: "2026-08-18T11:00:00+08:00",
      }],
      total: 1,
    });
    vi.spyOn(api, "mpArticleMetricsBatch").mockResolvedValue({
      total: 2,
      succeeded: 1,
      failed: 1,
      items: [
        { index: 0, url: "https://mp.weixin.qq.com/s/first", ok: true, data: { title: "第一篇", read_count: 120, wow_count: 8, like_count: 5, comment_count: 2 } },
        { index: 1, url: "https://mp.weixin.qq.com/s/second", ok: false, error: { detail: "Token 已过期" }, data: null },
      ],
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("collects real article metrics and keeps token values redacted", async () => {
    renderPage();

    expect(await screen.findByText("key1...key9")).toBeVisible();
    expect(screen.getByText("appm...oken")).toBeVisible();
    expect(screen.getByText("完整可用")).toBeVisible();
    expect(screen.getByText("已捕获 1 篇")).toBeVisible();
    fireEvent.change(screen.getByLabelText("公众号文章 URL"), { target: { value: "https://mp.weixin.qq.com/s/article" } });
    fireEvent.click(screen.getByRole("button", { name: "采集指标" }));

    await waitFor(() => expect(api.mpArticleMetrics).toHaveBeenCalledWith({
      url: "https://mp.weixin.qq.com/s/article",
      fetch_missing: true,
      include_comments: true,
      comment_limit: 100,
      max_comment_pages: 3,
    }));
    expect(await screen.findByText("128,764")).toBeVisible();
    expect(screen.getByText("一篇真实文章")).toBeVisible();
    expect(screen.getByText("内容很有价值")).toBeVisible();
  });

  it("imports tokens and exports only the selected formats", async () => {
    renderPage();
    await screen.findByText("key1...key9");
    fireEvent.change(screen.getByLabelText(/^key/), { target: { value: "raw-key" } });
    fireEvent.change(screen.getByLabelText(/^uin/), { target: { value: "123456" } });
    fireEvent.change(screen.getByLabelText(/^pass_ticket/), { target: { value: "raw-ticket" } });
    fireEvent.change(screen.getByLabelText(/^appmsg_token/), { target: { value: "raw-appmsg-token" } });
    fireEvent.click(screen.getByRole("button", { name: "安全导入" }));

    await waitFor(() => expect(api.mpTokenIngest).toHaveBeenCalledWith({ key: "raw-key", uin: "123456", pass_ticket: "raw-ticket", appmsg_token: "raw-appmsg-token" }, "manual-import"));
    expect(await screen.findByText(/服务端仅显示脱敏值/)).toBeVisible();

    fireEvent.change(screen.getByLabelText("公众号文章 URL"), { target: { value: "https://mp.weixin.qq.com/s/article" } });
    fireEvent.click(screen.getByLabelText(/HTML/));
    fireEvent.click(screen.getByRole("button", { name: "开始备份" }));

    await waitFor(() => expect(api.mpExport).toHaveBeenCalledWith({
      url: "https://mp.weixin.qq.com/s/article",
      formats: ["markdown", "pdf"],
      localize_resources: true,
    }));
    expect(await screen.findByRole("link", { name: /下载完整 ZIP/ })).toHaveAttribute("href", "/api/mp/exports/backup-1/backup.zip");
    expect(screen.getByRole("link", { name: /Markdown/ })).toBeVisible();
    expect(screen.getByRole("link", { name: /PDF/ })).toBeVisible();
  });

  it("collects competitor URLs in order and renders success and error rows", async () => {
    renderPage();
    expect(await screen.findByText("历史快照文章")).toBeVisible();
    fireEvent.change(screen.getByLabelText("竞品文章 URL（每行一条）"), {
      target: { value: "https://mp.weixin.qq.com/s/first\n\nhttps://mp.weixin.qq.com/s/second" },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始批量采集" }));

    await waitFor(() => expect(api.mpArticleMetricsBatch).toHaveBeenCalledWith({
      urls: ["https://mp.weixin.qq.com/s/first", "https://mp.weixin.qq.com/s/second"],
    }));
    expect(await screen.findByText("第一篇")).toBeVisible();
    expect(screen.getByText("Token 已过期")).toBeVisible();
    expect(screen.getByText("成功 1")).toBeVisible();
    expect(screen.getByText("失败 1")).toBeVisible();
  });

  it("rejects non-HTTPS and noncanonical article URLs before API calls", async () => {
    renderPage();
    await screen.findByText("key1...key9");
    const input = screen.getByLabelText("公众号文章 URL");

    fireEvent.change(input, { target: { value: "http://mp.weixin.qq.com/s/article" } });
    fireEvent.click(screen.getByRole("button", { name: "采集指标" }));
    expect(await screen.findByText("仅支持 mp.weixin.qq.com 文章链接")).toBeVisible();

    fireEvent.change(input, { target: { value: "https://evil.mp.weixin.qq.com/s/article" } });
    fireEvent.click(screen.getByRole("button", { name: "采集指标" }));

    expect(api.mpArticleMetrics).not.toHaveBeenCalled();
  });

  it("turns unresolved short-link identity errors into an actionable message", async () => {
    vi.mocked(api.mpArticleMetrics).mockRejectedValueOnce(new ApiError(422, "article identity is missing biz, mid, idx"));
    renderPage();
    await screen.findByText("key1...key9");
    fireEvent.change(screen.getByLabelText("公众号文章 URL"), { target: { value: "https://mp.weixin.qq.com/s/short-link" } });
    fireEvent.click(screen.getByRole("button", { name: "采集指标" }));

    expect(await screen.findByText(/请先在微信客户端打开或刷新文章/)).toBeVisible();
  });
});
