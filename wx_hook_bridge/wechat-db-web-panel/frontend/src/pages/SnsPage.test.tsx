import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import { SnsPage } from "./SnsPage";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<Outlet context={{ profile: undefined, profileName: "本地账号" }} />}>
            <Route path="/" element={<SnsPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SnsPage", () => {
  beforeEach(() => {
    vi.spyOn(api, "sns").mockResolvedValue({
      items: [],
      total: 0,
      stats: { posts: 0, users: 0, comments: 0, likes: 0, media: 0 },
    });
    vi.spyOn(api, "snsStats").mockResolvedValue({ posts: 0, users: 0, comments: 0, likes: 0, media: 0 });
    vi.spyOn(api, "snsSyncStatus").mockResolvedValue({ enabled: true, configured: true, generation: 1 });
    vi.spyOn(api, "snsSync").mockResolvedValue({ enabled: true, configured: true, generation: 2, changed: true });
    vi.spyOn(api, "snsBackups").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(api, "snsDetail").mockResolvedValue({ sns_id: "sns-test", media: [] });
    vi.spyOn(api, "snsBackup").mockResolvedValue({
      backup_id: "sns-test",
      download_url: "/api/sns/backups/sns-test/download",
    });
    vi.spyOn(api, "snsDeleteBackup").mockResolvedValue({ deleted: true });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("does not show a backup error before a backup is attempted", async () => {
    renderPage();

    expect(await screen.findByText("朋友圈动态")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the backup error only after the backup mutation fails", async () => {
    vi.mocked(api.snsBackup).mockRejectedValueOnce(new Error("备份服务不可用"));
    renderPage();

    await screen.findByText("朋友圈动态");
    screen.getByRole("button", { name: "生成备份" }).click();

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("备份服务不可用"));
  });

  it("requests a live snapshot refresh from the feed toolbar", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "同步最新" }));

    await waitFor(() => expect(api.snsSync).toHaveBeenCalledTimes(1));
  });

  it("renders a media grid and opens images in the detail lightbox", async () => {
    const post = {
      sns_id: "sns-media",
      nickname: "旅行者",
      create_time: "2026-08-12T14:35:00Z",
      content: "海边的下午",
      visibility: "public",
      media_count: 2,
      comment_count: 1,
      like_count: 2,
      media: [
        { id: "photo", type: 2, preview_url: "/api/sns/media/photo?variant=preview", media_url: "/api/sns/media/photo", available: true, browser_supported: true },
        { id: "movie", type: 6, preview_url: "/api/sns/media/movie?variant=preview", media_url: "/api/sns/media/movie", duration: 18, available: true, browser_supported: true },
      ],
    };
    vi.mocked(api.sns).mockResolvedValue({ items: [post], total: 1, stats: { posts: 1, media: 2 } });
    vi.mocked(api.snsStats).mockResolvedValue({ posts: 1, users: 1, comments: 1, likes: 2, media: 2 });
    vi.mocked(api.snsDetail).mockResolvedValue({ ...post, comments: [], likes: [] });
    renderPage();

    expect(await screen.findByRole("img", { name: "朋友圈图片 1" })).toHaveAttribute("src", "/api/sns/media/photo?variant=preview");
    expect(screen.getByRole("img", { name: "朋友圈视频封面 2" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "查看图片 1" }));

    const dialog = await screen.findByRole("dialog", { name: "朋友圈动态详情" });
    expect(within(dialog).getByRole("img", { name: "朋友圈媒体 1" })).toHaveAttribute("src", "/api/sns/media/photo");
    expect(within(dialog).getByRole("link", { name: "打开原始媒体" })).toHaveAttribute("href", "/api/sns/media/photo");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "朋友圈动态详情" })).not.toBeInTheDocument());
  });

  it("opens a playable video from the feed without resetting the selected media", async () => {
    const post = {
      sns_id: "sns-video",
      nickname: "拍摄者",
      content: "视频记录",
      media: [
        { id: "photo", type: 2, preview_url: "/api/sns/media/photo-preview", media_url: "/api/sns/media/photo", available: true },
        { id: "movie", type: 6, preview_url: "/api/sns/media/movie-preview", media_url: "/api/sns/media/movie", duration: 9, available: true, browser_supported: true },
      ],
    };
    vi.mocked(api.sns).mockResolvedValue({ items: [post], total: 1 });
    vi.mocked(api.snsDetail).mockResolvedValue(post);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "打开视频 2" }));
    const dialog = await screen.findByRole("dialog", { name: "朋友圈动态详情" });
    const video = dialog.querySelector("video");
    expect(video).toHaveAttribute("src", "/api/sns/media/movie");
    expect(video).toHaveAttribute("controls");
    expect(within(dialog).getByText("2 / 2")).toBeVisible();
  });

  it("does not fall back to remote CDN media when only a local poster exists", async () => {
    const post = {
      sns_id: "sns-poster-only",
      nickname: "拍摄者",
      content: "只有本地封面",
      media: [
        {
          id: "movie",
          type: 6,
          preview_url: "/api/sns/media/movie?variant=preview",
          url: "https://remote.invalid/movie.mp4",
          thumb_url: "https://remote.invalid/poster.jpg",
          available: true,
          browser_supported: true,
        },
      ],
    };
    vi.mocked(api.sns).mockResolvedValue({ items: [post], total: 1 });
    vi.mocked(api.snsDetail).mockResolvedValue(post);
    renderPage();

    expect(await screen.findByText("仅封面")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "查看视频封面 1" }));

    const dialog = await screen.findByRole("dialog", { name: "朋友圈动态详情" });
    expect(within(dialog).getByRole("img", { name: "朋友圈媒体 1" })).toHaveAttribute(
      "src",
      "/api/sns/media/movie?variant=preview",
    );
    expect(dialog.querySelector("video")).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("link", { name: "打开原始媒体" })).not.toBeInTheDocument();
  });
});
