import { useDeferredValue, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Copy, ExternalLink, FileText, Image as ImageIcon, Link2, Play, Search, Video } from "lucide-react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { DonutChart, WordCloud } from "../components/charts";
import { Card, EmptyState, ErrorState, LoadingState, ProfileBanner, SectionLink } from "../components/ui";
import type { ShellContext } from "../components/AppShell";
import { api, assetUrl, MediaItem } from "../lib/api";
import { formatDateTime, formatNumber, safeText } from "../lib/format";

function formatBytes(value?: number) {
  if (typeof value !== "number") return "—";
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
  return `${size.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function mediaTitle(item: MediaItem) {
  return item.file_name || item.title || `${item.kind} #${item.id}`;
}

export function MediaPage() {
  const navigate = useNavigate();
  const { profile } = useOutletContext<ShellContext>();
  const [mode, setMode] = useState<"cloud" | "list">("cloud");
  const [previewKind, setPreviewKind] = useState<"all" | "image" | "video" | "file">("all");
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search.trim());
  const [lightbox, setLightbox] = useState<MediaItem>();
  const lightboxTrigger = useRef<HTMLButtonElement | null>(null);
  const lightboxClose = useRef<HTMLButtonElement>(null);
  const query = useQuery({ queryKey: ["media", 40], queryFn: ({ signal }) => api.media({ limit: 40 }, signal) });
  const contentSearchQuery = useQuery({ queryKey: ["media-content-search", deferredSearch], queryFn: ({ signal }) => api.search(deferredSearch, "messages", signal), enabled: Boolean(deferredSearch) });
  const data = query.data;
  const items = data?.items ?? [];
  const previews = items.filter((item) => ["image", "video", "file"].includes(item.kind) && (previewKind === "all" || item.kind === previewKind)).slice(0, 10);
  const files = data?.files?.length ? data.files : items.filter((item) => item.kind === "file");
  const links = data?.links?.length ? data.links : items.filter((item) => item.kind === "link");
  const ocr = data?.ocr ?? items.filter((item) => item.kind === "ocr");
  const resourceSnippets = items.slice(0, 4);

  function closeLightbox() {
    setLightbox(undefined);
    requestAnimationFrame(() => lightboxTrigger.current?.focus());
  }

  useEffect(() => {
    if (!lightbox) return;
    lightboxClose.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") closeLightbox(); };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [lightbox]);

  if (query.isLoading) return <LoadingState label="正在索引本地媒体记录" />;
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />;

  const counts = data?.counts ?? {};
  const keywordTop = data?.keywords?.slice(0, 5) ?? [];

  return (
    <div className="page media-page">
      <ProfileBanner
        profile={profile}
        subtitle="媒体与关键词 / 内容分析"
        tags={["联系人", "真实资源", "本地分析"]}
        metrics={[
          { label: "图片数量", value: formatNumber(counts.images), suffix: "张", icon: <ImageIcon /> },
          { label: "视频数量", value: formatNumber(counts.videos), suffix: "个", icon: <Video /> },
          { label: "文件数量", value: formatNumber(counts.files), suffix: "个", icon: <FileText /> },
          { label: "链接分享", value: formatNumber(counts.links), suffix: "条", icon: <Link2 /> },
          { label: "高频关键词 Top5", value: keywordTop.length ? <span className="keyword-chips">{keywordTop.map((item) => <i key={item.word}>{item.word} {formatNumber(item.count)}</i>)}</span> : "—" },
        ]}
      />

      <div className="media-grid-top">
        <Card className="preview-card" title="近期媒体预览" action={<SectionLink>查看全部</SectionLink>}>
          <div className="media-tabs">{([["all", "全部"], ["image", "图片"], ["video", "视频"], ["file", "文件"]] as const).map(([value, label]) => <button className={previewKind === value ? "active" : ""} type="button" key={value} onClick={() => setPreviewKind(value)}>{label}</button>)}</div>
          {previews.length ? <div className="media-mosaic">
            {previews.map((item, index) => {
              const src = item.browser_supported === false ? undefined : assetUrl(item.thumbnail_url || (item.kind === "image" ? item.url : undefined));
              return <button type="button" className={`media-tile tile-${index}`} key={`${item.kind}-${item.id}`} onClick={(event) => { if (item.available && item.browser_supported !== false) { lightboxTrigger.current = event.currentTarget; setLightbox(item); } }}>
                {src ? <img src={src} alt={mediaTitle(item)} loading="lazy" /> : <span className="resource-missing"><FileText size={25} />{item.browser_supported === false ? "微信 DAT 格式不可直接预览" : item.available ? mediaTitle(item) : "本地资源缺失"}</span>}
                {item.kind === "video" && <i className="play"><Play size={18} fill="currentColor" /></i>}
                {item.kind === "file" && <b className="file-badge">{item.file_name?.split(".").pop()?.toUpperCase() || "FILE"}</b>}
              </button>;
            })}
          </div> : <EmptyState title="暂无可预览媒体" detail="数据库索引存在但文件缺失时不会生成替代图片。" />}
        </Card>

        <Card className="keywords-card" title="高频关键词" action={<div className="segmented"><button type="button" className={mode === "cloud" ? "active" : ""} onClick={() => setMode("cloud")}>词云</button><button type="button" className={mode === "list" ? "active" : ""} onClick={() => setMode("list")}>列表</button></div>}>
          <div className="keywords-content">
            <div className="wordcloud-wrap">{mode === "cloud" ? <WordCloud data={data?.keywords} height={245} /> : <div className="keyword-bars">{data?.keywords?.slice(0, 12).map((item) => <div key={item.word}><span>{item.word}</span><i><b style={{ width: `${(item.count / Math.max(1, data.keywords?.[0]?.count ?? 1)) * 100}%` }} /></i><em>{formatNumber(item.count)}</em></div>)}</div>}</div>
            <div className="keyword-ranking"><div><span>关键词</span><span>出现次数</span></div>{data?.keywords?.slice(0, 10).map((item, index) => <p key={item.word}><i>{index + 1}</i><b>{item.word}</b><span>{formatNumber(item.count)}</span></p>)}</div>
          </div>
        </Card>
      </div>

      <div className="media-grid-middle">
        <Card title="内容主题分布" action={<SectionLink>查看全部</SectionLink>}>
          <DonutChart data={data?.topics} height={190} label="内容主题分布" />
        </Card>
        <Card title="文件列表" action={<SectionLink>查看全部</SectionLink>}>
          {files.length ? <div className="file-table"><div><span>文件名</span><span>类型</span><span>大小</span><span>最近时间</span></div>{files.slice(0, 5).map((item) => <button type="button" key={item.id} onClick={() => item.url && window.open(assetUrl(item.url), "_blank", "noopener,noreferrer")} disabled={!item.available}><span><FileText size={15} />{mediaTitle(item)}</span><span>{item.file_name?.split(".").pop()?.toUpperCase() || item.kind}</span><span>{formatBytes(item.file_size)}</span><span>{formatDateTime(item.modify_time || item.create_time)}</span></button>)}</div> : <EmptyState title="暂无文件记录" />}
        </Card>
      </div>

      <div className="media-grid-bottom">
        <Card title="内容搜索与片段">
          <label className="inline-search"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索内容、关键词或文件名" /></label>
          {deferredSearch ? contentSearchQuery.isLoading ? <LoadingState label="正在查询本地消息索引" /> : contentSearchQuery.isError ? <ErrorState error={contentSearchQuery.error} retry={() => contentSearchQuery.refetch()} /> : contentSearchQuery.data?.items.length ? <div className="snippet-list">{contentSearchQuery.data.items.slice(0, 5).map((item, index) => <button type="button" key={safeText(item.id, String(index))} onClick={() => navigate(`/conversations?message=${encodeURIComponent(safeText(item.id, ""))}&session=${encodeURIComponent(safeText(item.session_username, ""))}&q=${encodeURIComponent(deferredSearch)}`)}><span className="snippet-icon"><FileText size={15} /></span><p><b>{safeText(item.title || item.session_name, "消息匹配")}</b><span>{safeText(item.snippet, "当前结果没有文本片段")}</span><small>{safeText(item.session_name, "会话")} · {formatDateTime(item.create_time)}</small></p></button>)}</div> : <EmptyState title="没有匹配结果" /> : resourceSnippets.length ? <div className="snippet-list">{resourceSnippets.map((item) => <div key={`${item.kind}-${item.id}`}><span className="snippet-icon"><FileText size={15} /></span><p><b>{mediaTitle(item)}</b><span>{safeText(item.snippet || item.text, "当前记录无可解码文本片段")}</span><small>{item.kind} · {formatDateTime(item.create_time || item.modify_time)}</small></p></div>)}</div> : <EmptyState title="暂无可检索片段" />}
        </Card>
        <Card title="链接分享记录" action={<SectionLink>查看全部</SectionLink>}>
          {links.length ? <div className="link-list">{links.slice(0, 6).map((item) => <button type="button" key={item.id} onClick={() => item.url && window.open(item.url, "_blank", "noopener,noreferrer")}><span>{mediaTitle(item)}</span><small>{item.source || "链接"}</small><time>{formatDateTime(item.create_time || item.modify_time)}</time><ExternalLink size={13} /></button>)}</div> : <EmptyState title="暂无链接分享记录" />}
        </Card>
        <Card title="文本提取 (OCR)" action={<SectionLink>查看全部</SectionLink>}>
          {ocr.length ? <div className="ocr-panel"><div className="ocr-preview">{ocr[0].thumbnail_url ? <img src={assetUrl(ocr[0].thumbnail_url)} alt="OCR 来源图片" /> : <ImageIcon size={32} />}</div><div className="ocr-text"><p>{safeText(ocr[0].text, "OCR 未识别出文本")}</p><small>识别置信度：{typeof ocr[0].confidence === "number" ? `${(ocr[0].confidence * 100).toFixed(1)}%` : "—"}</small><button type="button" onClick={() => navigator.clipboard.writeText(ocr[0].text || "")} disabled={!ocr[0].text}><Copy size={14} />复制文本</button></div></div> : <EmptyState title="暂无 OCR 结果" detail="仅展示本地 OCR 的真实输出。" />}
        </Card>
      </div>

      {lightbox && <div className="lightbox" role="dialog" aria-modal="true" aria-label={mediaTitle(lightbox)} onClick={closeLightbox}><button ref={lightboxClose} type="button" className="lightbox-close" onClick={closeLightbox}>关闭</button>{lightbox.kind === "video" && lightbox.url ? <video src={assetUrl(lightbox.url)} controls autoPlay onClick={(event) => event.stopPropagation()} /> : lightbox.url ? <img src={assetUrl(lightbox.url)} alt={mediaTitle(lightbox)} onClick={(event) => event.stopPropagation()} /> : <EmptyState title="资源不可用" />}</div>}
    </div>
  );
}
