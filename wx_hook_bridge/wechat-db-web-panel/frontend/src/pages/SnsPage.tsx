import { useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Download,
  ExternalLink,
  Heart,
  Image as ImageIcon,
  ImageOff,
  MapPin,
  Maximize2,
  MessageCircle,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  Video,
  X,
} from "lucide-react";
import { useOutletContext } from "react-router-dom";
import { Avatar, Card, EmptyState, ErrorState, LoadingState, Pill, ProfileBanner } from "../components/ui";
import type { ShellContext } from "../components/AppShell";
import { api, assetUrl, type SnsDetailResponse, type SnsMedia, type SnsPost } from "../lib/api";
import { formatDateTime, formatDuration, formatNumber, safeText } from "../lib/format";

const VIDEO_TYPES = new Set(["6", "video", "movie", "video_message"]);

function mediaKind(media: SnsMedia): "video" | "image" {
  const type = String(media.type ?? "").toLowerCase();
  const mime = String(media.mime_type ?? "").toLowerCase();
  return mime.startsWith("video/") || VIDEO_TYPES.has(type) || Number(media.duration ?? 0) > 0 ? "video" : "image";
}

function mediaToken(media: SnsMedia): string | undefined {
  const value = media.token || media.media_token;
  return value ? String(value) : undefined;
}

/** Resolve both old local-path responses and the newer token media contract. */
function snsMediaUrl(media: SnsMedia, variant: "preview" | "source" = "source"): string | undefined {
  const explicit = variant === "preview"
    ? media.preview_url || media.poster_url
    : media.media_url || media.download_url;
  if (explicit) return assetUrl(explicit);

  const canUseLocal = media.available !== false || media.resource_available === true;
  if (canUseLocal && media.local_path) return assetUrl(media.local_path);

  const token = mediaToken(media);
  const tokenOnlyContract = token
    && !media.preview_url
    && !media.poster_url
    && !media.media_url
    && !media.download_url;
  return canUseLocal && tokenOnlyContract
    ? assetUrl(`sns/media/${encodeURIComponent(token)}${variant === "preview" ? "?variant=preview" : ""}`)
    : undefined;
}

function mediaLabel(post: SnsPost) {
  const count = post.media_count ?? post.media?.length ?? 0;
  return count ? `${count} 项媒体` : null;
}

function postTime(post: SnsPost) {
  return formatDateTime(post.create_time || post.create_timestamp);
}

function authorName(post: SnsPost) {
  return safeText(post.nickname || post.username, "未知联系人");
}

function stopPropagation(event: { stopPropagation: () => void }) {
  event.stopPropagation();
}

export function SnsPage() {
  const { profile } = useOutletContext<ShellContext>();
  const client = useQueryClient();
  const [queryText, setQueryText] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<SnsPost>();
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [includeLocalMedia, setIncludeLocalMedia] = useState(true);
  const [includeRuntimeArtifacts, setIncludeRuntimeArtifacts] = useState(true);
  const limit = 24;

  const listQuery = useQuery({
    queryKey: ["sns", limit, offset, query],
    queryFn: ({ signal }) => api.sns({ limit, offset, q: query }, signal),
  });
  const statsQuery = useQuery({ queryKey: ["sns-stats"], queryFn: ({ signal }) => api.snsStats(signal) });
  const syncQuery = useQuery({
    queryKey: ["sns-sync-status"],
    queryFn: ({ signal }) => api.snsSyncStatus(signal),
    refetchInterval: 5_000,
    refetchIntervalInBackground: true,
    staleTime: 2_000,
  });
  const backupsQuery = useQuery({ queryKey: ["sns-backups"], queryFn: ({ signal }) => api.snsBackups(signal) });
  const detailQuery = useQuery({
    queryKey: ["sns-detail", selected?.sns_id],
    queryFn: ({ signal }) => api.snsDetail(selected!.sns_id, signal),
    enabled: Boolean(selected?.sns_id),
  });
  const backupMutation = useMutation({
    mutationFn: () => api.snsBackup({
      include_local_media: includeLocalMedia,
      include_runtime_artifacts: includeRuntimeArtifacts,
    }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["sns-backups"] }),
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.snsDeleteBackup(id),
    onSuccess: () => client.invalidateQueries({ queryKey: ["sns-backups"] }),
  });
  const syncMutation = useMutation({
    mutationFn: () => api.snsSync(),
    onSuccess: (status) => {
      client.setQueryData(["sns-sync-status"], status);
      void client.invalidateQueries({
        predicate: (item) => ["sns", "sns-stats", "sns-detail"].includes(String(item.queryKey[0] ?? "")),
        refetchType: "active",
      });
    },
  });

  const items = listQuery.data?.items ?? [];
  const stats = listQuery.data?.stats ?? statsQuery.data ?? {};
  const syncStatus = syncQuery.data ?? listQuery.data?.sync;
  const lastSyncGeneration = useRef<number>();
  const detail: SnsDetailResponse | undefined = detailQuery.data ?? selected;
  const detailMedia = detail?.media ?? [];
  const hasNext = offset + items.length < (listQuery.data?.total ?? 0);
  const hasPrevious = offset > 0;
  const backupBusy = backupMutation.isPending;
  const backupError = backupMutation.error
    ? backupMutation.error instanceof Error ? backupMutation.error.message : "备份失败"
    : undefined;

  useEffect(() => {
    const generation = syncStatus?.generation;
    if (typeof generation !== "number") return;
    if (lastSyncGeneration.current === undefined) {
      lastSyncGeneration.current = generation;
      return;
    }
    if (lastSyncGeneration.current === generation) return;
    lastSyncGeneration.current = generation;
    void client.invalidateQueries({
      predicate: (item) => ["sns", "sns-stats", "sns-detail"].includes(String(item.queryKey[0] ?? "")),
      refetchType: "active",
    });
  }, [client, syncStatus?.generation]);

  useEffect(() => {
    if (!listQuery.data?.stats) return;
    client.setQueryData(["sns-stats"], listQuery.data.stats);
  }, [client, listQuery.data?.stats]);

  useEffect(() => {
    if (!selected) return;
    const close = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setSelected(undefined);
      if (event.key === "ArrowLeft") setLightboxIndex((index) => Math.max(0, index - 1));
      if (event.key === "ArrowRight") setLightboxIndex((index) => Math.min(Math.max(0, detailMedia.length - 1), index + 1));
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", close);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", close);
    };
  }, [selected, detailMedia.length]);

  function openPost(post: SnsPost, index = 0) {
    setSelected(post);
    setLightboxIndex(index);
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    setOffset(0);
    setQuery(queryText.trim());
  }

  if (listQuery.isLoading || statsQuery.isLoading) return <LoadingState label="正在读取朋友圈快照" />;
  if (listQuery.isError) return <ErrorState error={listQuery.error} retry={() => listQuery.refetch()} />;

  return (
    <div className="page sns-page">
      <ProfileBanner
        profile={profile}
        subtitle="朋友圈 / 原始动态归档"
        tags={["只读快照", "媒体可预览", "保留原始 XML"]}
        metrics={[
          { label: "动态数量", value: formatNumber(stats.posts ?? listQuery.data?.total), suffix: "条", icon: <MessageCircle /> },
          { label: "涉及联系人", value: formatNumber(stats.users), suffix: "位", icon: <ShieldCheck /> },
          { label: "评论与点赞", value: formatNumber((stats.comments ?? 0) + (stats.likes ?? 0)), suffix: "条", icon: <Heart /> },
          { label: "媒体记录", value: formatNumber(stats.media), suffix: "项", icon: <ImageIcon /> },
        ]}
        action={<button className="green-button" type="button" onClick={() => backupMutation.mutate()} disabled={backupBusy}><Archive size={15} />{backupBusy ? "生成中" : "生成备份"}</button>}
      />

      <div className="sns-toolbar">
        <form className="sns-search" onSubmit={submitSearch} role="search">
          <Search size={16} />
          <input value={queryText} onChange={(event) => setQueryText(event.target.value)} placeholder="搜索正文、昵称或账号" aria-label="搜索朋友圈" />
          <button type="submit">搜索</button>
        </form>
        <div className={`sns-sync-state ${syncStatus?.last_error || syncQuery.isError ? "error" : syncStatus?.enabled ? "live" : "snapshot"}`} title={syncStatus?.last_error || undefined}>
          <span />
          {syncMutation.isPending
            ? "正在同步朋友圈"
            : syncStatus?.last_error || syncQuery.isError
              ? "自动同步异常"
              : syncStatus?.enabled
                ? `自动同步 · 最新动态 ${stats.last_create_time ? formatDateTime(stats.last_create_time * 1000) : "读取中"}`
                : "静态快照"}
        </div>
      </div>
      {listQuery.data?.metric_basis && <p className="sns-basis">{listQuery.data.metric_basis}</p>}

      <div className="sns-layout">
        <Card className="sns-feed-card" title={<><span>朋友圈动态</span><small className="sns-feed-count">{formatNumber(listQuery.data?.total ?? 0)} 条</small></>} action={<button className="outline-button" type="button" onClick={() => syncMutation.mutate()} disabled={syncMutation.isPending || listQuery.isFetching}><RefreshCw size={14} className={syncMutation.isPending || listQuery.isFetching ? "spin" : ""} />{syncMutation.isPending ? "同步中" : "同步最新"}</button>}>
          {items.length ? <div className="sns-feed" aria-label="朋友圈动态列表">
            {items.map((post) => <SnsPostCard key={`${post.sns_id}-${post.tid ?? ""}`} post={post} ownerAvatar={post.is_owner ? profile?.avatar_url : undefined} onOpen={openPost} />)}
          </div> : <EmptyState title="暂无朋友圈动态" detail={query ? "没有匹配当前搜索条件的记录。" : "当前快照没有可展示的 SNS 数据。"} />}
          <div className="sns-pagination">
            <span>{listQuery.data?.total ? `${offset + 1}-${Math.min(offset + items.length, listQuery.data.total)} / ${listQuery.data.total}` : "0 条"}</span>
            <div><button type="button" className="outline-button" disabled={!hasPrevious} onClick={() => setOffset(Math.max(0, offset - limit))}>上一页</button><button type="button" className="outline-button" disabled={!hasNext} onClick={() => setOffset(offset + limit)}>下一页</button></div>
          </div>
        </Card>

        <Card className="sns-backup-card" title={<><Archive size={16} />备份记录</>} action={<Pill tone="green">ZIP</Pill>}>
          <p className="sns-backup-intro">包含 SQLite 原库、动态 JSONL、互动记录和校验清单。勾选本地媒体后，归档会携带可预览的图片、视频与背景缓存。</p>
          <div className="sns-backup-options">
            <label><input type="checkbox" checked={includeLocalMedia} onChange={(event) => setIncludeLocalMedia(event.target.checked)} />复制本机 SNS 图片、视频与背景缓存</label>
            <label><input type="checkbox" checked={includeRuntimeArtifacts} onChange={(event) => setIncludeRuntimeArtifacts(event.target.checked)} />附带有大小上限的原生 SNS 诊断记录</label>
          </div>
          {backupError && <p className="sns-error" role="alert">{backupError}</p>}
          {backupsQuery.isLoading ? <LoadingState label="读取备份记录" /> : backupsQuery.isError ? <ErrorState error={backupsQuery.error} retry={() => backupsQuery.refetch()} /> : backupsQuery.data?.items?.length ? <div className="sns-backups">{backupsQuery.data.items.map((backup) => <div className="sns-backup-row" key={backup.backup_id}><div><b>{backup.backup_id}</b><small>{formatDateTime(backup.created_at)} · {formatNumber(backup.bytes)} bytes</small><small>{backup.sha256 ? `SHA-256 ${backup.sha256.slice(0, 16)}…` : "已生成校验清单"}</small></div><div><a className="icon-button" href={assetUrl(backup.download_url)} download aria-label={`下载备份 ${backup.backup_id}`}><Download size={15} /></a><button className="icon-button danger" type="button" onClick={() => { if (window.confirm(`删除备份 ${backup.backup_id}？`)) deleteMutation.mutate(backup.backup_id); }} disabled={deleteMutation.isPending} aria-label={`删除备份 ${backup.backup_id}`}><Trash2 size={15} /></button></div></div>)}</div> : <EmptyState title="还没有备份" detail="点击上方生成第一份朋友圈归档。" />}
        </Card>
      </div>

      {backupMutation.data && <div className="sns-toast" role="status">备份已生成：<a href={assetUrl(backupMutation.data.download_url)} download>{backupMutation.data.backup_id}</a></div>}
      {detail && <SnsLightbox detail={detail} loading={detailQuery.isFetching} index={Math.min(lightboxIndex, Math.max(0, detailMedia.length - 1))} ownerAvatar={detail.is_owner ? profile?.avatar_url : undefined} onIndexChange={setLightboxIndex} onClose={() => setSelected(undefined)} />}
    </div>
  );
}

function SnsPostCard({ post, ownerAvatar, onOpen }: { post: SnsPost; ownerAvatar?: string; onOpen: (post: SnsPost, index?: number) => void }) {
  const media = post.media ?? [];
  const name = authorName(post);
  return (
    <article className="sns-post-row">
      <div className="sns-post-marker" aria-hidden="true"><span /></div>
      <div className="sns-post-body">
        <button className="sns-post-main" type="button" onClick={() => onOpen(post)} aria-label={`打开 ${name} 的朋友圈动态`}>
          <div className="sns-post-meta">
            <Avatar src={post.avatar_url || ownerAvatar} name={name} size={42} className="sns-post-avatar" />
            <span className="sns-post-author"><b>{name}</b><time><Clock3 size={11} />{postTime(post)}</time></span>
            <Pill tone={post.private ? "orange" : "neutral"}>{post.private ? "私密" : safeText(post.visibility, "公开")}</Pill>
          </div>
          <p>{safeText(post.content, "（图片 / 视频动态）")}</p>
        </button>
        {media.length > 0 && <SnsMediaGrid media={media} onOpen={(index) => onOpen(post, index)} />}
        <div className="sns-post-footer">
          <span><MessageCircle size={13} /> {formatNumber(post.comment_count ?? post.comments?.length ?? 0)}</span>
          <span><Heart size={13} /> {formatNumber(post.like_count ?? post.likes?.length ?? 0)}</span>
          {post.location && <span className="sns-location-chip"><MapPin size={13} /> {safeText(post.location.name || post.location.address, "位置")}</span>}
          <span className="sns-id">ID {post.sns_id}</span>
          <button className="sns-post-detail-link" type="button" onClick={() => onOpen(post)}>查看详情 <ChevronRight size={13} /></button>
        </div>
      </div>
    </article>
  );
}

function SnsMediaGrid({ media, onOpen }: { media: SnsMedia[]; onOpen: (index: number) => void }) {
  const countClass = media.length === 1 ? "single" : media.length === 2 || media.length === 4 ? "double" : "triple";
  return <div className={`sns-media-grid ${countClass}`} aria-label={`${media.length} 项朋友圈媒体`}>{media.map((item, index) => <SnsMediaTile key={`${item.id ?? item.md5 ?? index}`} media={item} index={index} onOpen={onOpen} />)}</div>;
}

function SnsMediaTile({ media, index, onOpen }: { media: SnsMedia; index: number; onOpen: (index: number) => void }) {
  const [failed, setFailed] = useState(false);
  const kind = mediaKind(media);
  const preview = snsMediaUrl(media, "preview");
  const source = snsMediaUrl(media, "source");
  const playable = kind === "video" && Boolean(source);
  const visible = Boolean(preview) && !failed;
  return (
    <button className={`sns-media-tile ${visible ? "has-preview" : "is-placeholder"}`} type="button" onClick={() => onOpen(index)} aria-label={`${kind === "video" ? (playable ? "打开视频" : "查看视频封面") : "查看图片"} ${index + 1}`}>
      {visible ? <><img src={preview} alt={kind === "video" ? `朋友圈视频封面 ${index + 1}` : `朋友圈图片 ${index + 1}`} loading="lazy" onError={() => setFailed(true)} /><span className="sns-media-shade" />{kind === "video" && <span className="sns-video-badge">{playable ? <Play size={14} fill="currentColor" /> : <ImageIcon size={14} />}{playable ? (media.duration ? formatDuration(Number(media.duration)) : "视频") : "仅封面"}</span>}<span className="sns-media-expand"><Maximize2 size={14} /></span></> : <MediaPlaceholder media={media} compact source={source} />}
    </button>
  );
}

function MediaPlaceholder({ media, compact = false, source }: { media: SnsMedia; compact?: boolean; source?: string }) {
  const video = mediaKind(media) === "video";
  return <span className={`sns-media-placeholder ${compact ? "compact" : ""}`}><span className="sns-media-placeholder-icon">{video ? <Video size={compact ? 19 : 28} /> : <ImageOff size={compact ? 19 : 28} />}</span><b>{video ? "视频暂不可预览" : "图片暂不可预览"}</b><small>{source ? "打开详情后可尝试播放或下载" : "本地缓存未找到可解码文件"}</small></span>;
}

function SnsLightbox({ detail, loading, index, ownerAvatar, onIndexChange, onClose }: { detail: SnsDetailResponse; loading: boolean; index: number; ownerAvatar?: string; onIndexChange: (index: number) => void; onClose: () => void }) {
  const media = detail.media ?? [];
  const active = media[index];
  const name = authorName(detail);
  const source = active ? snsMediaUrl(active, "source") : undefined;
  const preview = active ? snsMediaUrl(active, "preview") : undefined;
  const imageSource = source || preview;
  const video = active ? mediaKind(active) === "video" : false;
  return (
    <div className="sns-detail-overlay" role="dialog" aria-modal="true" aria-label="朋友圈动态详情" onClick={onClose}>
      <section className="sns-lightbox" onClick={stopPropagation}>
        <div className="sns-lightbox-stage">
          <header className="sns-lightbox-toolbar"><span>{media.length ? `${index + 1} / ${media.length}` : "朋友圈详情"}</span><button className="sns-lightbox-action" type="button" onClick={onClose} aria-label="关闭朋友圈详情"><X size={19} /></button></header>
          <div className="sns-lightbox-canvas">
            {media.length ? <>{video && source && active.browser_supported !== false ? <video className="sns-lightbox-video" src={source} poster={preview} controls preload="metadata" /> : imageSource ? <img className="sns-lightbox-image" src={imageSource} alt={`朋友圈媒体 ${index + 1}`} /> : <MediaPlaceholder media={active!} source={source} />}{media.length > 1 && <><button className="sns-lightbox-nav previous" type="button" onClick={() => onIndexChange(Math.max(0, index - 1))} disabled={index === 0} aria-label="上一项媒体"><ChevronLeft size={23} /></button><button className="sns-lightbox-nav next" type="button" onClick={() => onIndexChange(Math.min(media.length - 1, index + 1))} disabled={index === media.length - 1} aria-label="下一项媒体"><ChevronRight size={23} /></button></>}</> : <div className="sns-lightbox-empty"><ImageIcon size={34} /><b>这条动态没有媒体</b><span>正文和互动记录仍然保留</span></div>}
          </div>
          {media.length > 0 && <div className="sns-lightbox-thumbs" aria-label="媒体缩略图列表">{media.map((item, itemIndex) => <button type="button" key={`${item.id ?? item.md5 ?? itemIndex}`} className={itemIndex === index ? "active" : ""} onClick={() => onIndexChange(itemIndex)} aria-label={`选择媒体 ${itemIndex + 1}`}>{snsMediaUrl(item, "preview") ? <img src={snsMediaUrl(item, "preview")} alt="" loading="lazy" /> : <span>{mediaKind(item) === "video" ? <Video size={16} /> : <ImageOff size={16} />}</span>}</button>)}</div>}
        </div>

        <aside className="sns-lightbox-info">
          <header className="sns-detail-header"><div className="sns-detail-author"><Avatar src={detail.avatar_url || ownerAvatar} name={name} size={45} className="sns-detail-avatar" /><div><h2>{name}</h2><time>{postTime(detail)}</time></div></div><Pill tone={detail.private ? "orange" : "green"}>{detail.private ? "私密" : safeText(detail.visibility, "公开")}</Pill></header>
          {loading && <p className="sns-muted">正在载入完整互动记录…</p>}
          <p className="sns-detail-content">{safeText(detail.content, "（图片 / 视频动态）")}</p>
          {detail.location && <p className="sns-location"><MapPin size={14} />{safeText(detail.location.name || detail.location.address, "位置记录")}</p>}
          {active && source && <div className="sns-lightbox-file-actions"><a href={source} target="_blank" rel="noreferrer"><ExternalLink size={14} />打开原始媒体</a><a href={source} download><Download size={14} />下载</a></div>}
          <div className="sns-detail-stats"><span><MessageCircle size={14} /> {formatNumber(detail.comment_count ?? detail.comments?.length ?? 0)} 评论</span><span><Heart size={14} /> {formatNumber(detail.like_count ?? detail.likes?.length ?? 0)} 点赞</span><span><ImageIcon size={14} /> {mediaLabel(detail) || "无媒体"}</span></div>
          {(detail.comments?.length || detail.likes?.length) ? <div className="sns-interactions">{detail.comments?.map((comment, commentIndex) => <p key={`${comment.id ?? comment.comment_id ?? commentIndex}`}><b>{safeText(comment.nickname || comment.username, "联系人")}</b>：{safeText(comment.content, "（无文字评论）")}</p>)}{detail.likes?.length ? <small>点赞：{detail.likes.map((like) => safeText(like.nickname || like.username, "联系人")).join("、")}</small> : null}{detail.raw_interaction_count ? <small>另保留 {formatNumber(detail.raw_interaction_count)} 条原始互动记录于备份中</small> : null}</div> : <p className="sns-muted">暂无已解析的评论或点赞</p>}
        </aside>
      </section>
    </div>
  );
}
