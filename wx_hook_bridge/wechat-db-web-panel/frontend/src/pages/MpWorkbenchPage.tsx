import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BarChart3,
  Check,
  ClipboardPaste,
  Code2,
  Download,
  Eye,
  FileDown,
  FileText,
  History,
  Heart,
  KeyRound,
  Link2,
  ListOrdered,
  LoaderCircle,
  MessageSquare,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UserRound,
} from "lucide-react";
import {
  ApiError,
  MPArticleHistoryResponse,
  MPArticleMetricsBatchItem,
  MPArticleMetricsBatchResponse,
  MPArticleMetricsResponse,
  MPExportFormat,
  MPExportResponse,
  MPTokenStatus,
  MPTokenFieldName,
  MPTokenInput,
  api,
  assetUrl,
} from "../lib/api";
import { formatDateTime, formatNumber, safeText } from "../lib/format";

type TokenFormFieldName = MPTokenFieldName | "appmsg_token";

const TOKEN_FIELDS: Array<{ name: TokenFormFieldName; label: string; hint: string }> = [
  { name: "key", label: "key", hint: "请求签名参数" },
  { name: "uin", label: "uin", hint: "当前微信账号标识" },
  { name: "pass_ticket", label: "pass_ticket", hint: "会话票据" },
  { name: "appmsg_token", label: "appmsg_token", hint: "阅读指标辅助令牌" },
];

const EXPORT_OPTIONS: Array<{ value: MPExportFormat; label: string; detail: string; icon: typeof FileText }> = [
  { value: "markdown", label: "Markdown", detail: ".md，适合知识库与版本管理", icon: FileText },
  { value: "html", label: "HTML", detail: ".html，保留文章结构与本地资源", icon: Code2 },
  { value: "pdf", label: "PDF", detail: ".pdf，适合归档与打印", icon: FileDown },
];

function messageFromError(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  if (message.includes("article identity is missing")) {
    return "短链接页面未返回文章身份，请先在微信客户端打开或刷新文章，等待 Token 自动捕获后重试";
  }
  if (message.includes("does not contain article metrics") || message.includes("no article metrics found")) {
    return "微信未返回阅读指标，请在微信客户端打开或刷新文章，确认 appmsg_token 已捕获后重试";
  }
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "请求失败，请检查本机服务状态后重试";
}

function tokenFieldStatus(status: MPTokenStatus | undefined, name: TokenFormFieldName) {
  return name === "appmsg_token" ? status?.auxiliary?.appmsg_token : status?.fields?.[name];
}

function isArticleUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    const host = parsed.hostname.toLowerCase().replace(/\.$/, "");
    return parsed.protocol === "https:"
      && host === "mp.weixin.qq.com"
      && !parsed.username
      && !parsed.password
      && (!parsed.port || parsed.port === "443");
  } catch {
    return false;
  }
}

function monitorUrlsFromText(value: string): string[] {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

function batchItemsFromResponse(response?: MPArticleMetricsBatchResponse): MPArticleMetricsBatchItem[] {
  return [...(response?.items ?? [])].sort((left, right) => left.index - right.index);
}

function batchItemData(item: MPArticleMetricsBatchItem): MPArticleMetricsResponse {
  return item.data ?? item;
}

function historyItemsFromResponse(response?: MPArticleHistoryResponse): MPArticleMetricsResponse[] {
  return response?.items ?? [];
}

function MetricTile({ icon: Icon, label, value, tone = "green" }: { icon: typeof Eye; label: string; value: unknown; tone?: string }) {
  return (
    <div className={`mp-metric-tile ${tone}`}>
      <span className="mp-metric-icon" aria-hidden="true"><Icon size={18} /></span>
      <span className="mp-metric-label">{label}</span>
      <strong>{formatNumber(value)}</strong>
    </div>
  );
}

function TokenState({ name, present, value, expired }: { name: string; present: boolean; value?: string | null; expired: boolean }) {
  return (
    <li className={`mp-token-row ${present ? "present" : "missing"}`}>
      <span className="mp-token-name"><KeyRound size={14} aria-hidden="true" />{name}</span>
      <code>{present ? safeText(value, "••••••••") : "未捕获"}</code>
      <span className="mp-token-state">{expired ? "已过期" : present ? "已脱敏" : "待捕获"}</span>
    </li>
  );
}

export function MpWorkbenchPage() {
  const queryClient = useQueryClient();
  const [url, setUrl] = useState("");
  const [urlError, setUrlError] = useState("");
  const [metrics, setMetrics] = useState<MPArticleMetricsResponse>();
  const [tokenForm, setTokenForm] = useState<MPTokenInput>({});
  const [tokenSource, setTokenSource] = useState("manual-import");
  const [tokenMessage, setTokenMessage] = useState("");
  const [tokenError, setTokenError] = useState("");
  const [formats, setFormats] = useState<MPExportFormat[]>(["markdown", "html", "pdf"]);
  const [exportResult, setExportResult] = useState<MPExportResponse>();
  const [exportError, setExportError] = useState("");
  const [monitorUrls, setMonitorUrls] = useState("");
  const [batchResult, setBatchResult] = useState<MPArticleMetricsBatchResponse>();
  const [batchError, setBatchError] = useState("");

  const tokenQuery = useQuery({
    queryKey: ["mp-tokens"],
    queryFn: ({ signal }) => api.mpTokenStatus(signal),
  });
  const historyQuery = useQuery({
    queryKey: ["mp-article-history"],
    queryFn: ({ signal }) => api.mpArticleHistory({ limit: 8 }, signal),
    staleTime: 30_000,
  });
  const metricsMutation = useMutation({
    mutationFn: (articleUrl: string) => api.mpArticleMetrics({
      url: articleUrl,
      fetch_missing: true,
      include_comments: true,
      comment_limit: 100,
      max_comment_pages: 3,
    }),
    onSuccess: (data) => {
      setMetrics(data);
      setUrlError("");
      void queryClient.invalidateQueries({ queryKey: ["mp-tokens"] });
    },
  });
  const tokenMutation = useMutation({
    mutationFn: ({ values, source }: { values: MPTokenInput; source: string }) => api.mpTokenIngest(values, source),
    onSuccess: (result) => {
      setTokenForm({});
      setTokenError("");
      setTokenMessage(result.captured.length ? `已导入 ${result.captured.join("、")}，服务端仅显示脱敏值` : "没有识别到可用 Token");
      void queryClient.invalidateQueries({ queryKey: ["mp-tokens"] });
    },
    onError: (error) => {
      setTokenMessage("");
      setTokenError(messageFromError(error));
    },
  });
  const clearTokenMutation = useMutation({
    mutationFn: () => api.mpTokenClear(),
    onSuccess: () => {
      setTokenMessage("动态 Token 已清除");
      setTokenError("");
      void queryClient.invalidateQueries({ queryKey: ["mp-tokens"] });
    },
    onError: (error) => setTokenError(messageFromError(error)),
  });
  const exportMutation = useMutation({
    mutationFn: () => api.mpExport({ url: url.trim(), formats, localize_resources: true }),
    onSuccess: (data) => {
      setExportResult(data);
      setExportError("");
    },
    onError: (error) => {
      setExportResult(undefined);
      setExportError(messageFromError(error));
    },
  });
  const batchMutation = useMutation({
    mutationFn: (urls: string[]) => api.mpArticleMetricsBatch({ urls }),
    onSuccess: (data) => {
      setBatchResult(data);
      setBatchError("");
      void queryClient.invalidateQueries({ queryKey: ["mp-article-history"] });
    },
    onError: (error) => {
      setBatchResult(undefined);
      setBatchError(messageFromError(error));
    },
  });

  const tokenStatus = tokenQuery.data?.token;
  const tokenSources = tokenQuery.data?.sources;
  const nativeContextAvailable = tokenSources?.native_article_context_available === true;
  const nativeContextCount = typeof tokenSources?.native_article_context_count === "number"
    ? tokenSources.native_article_context_count
    : 0;
  const nativeContextCapturedAt = typeof tokenSources?.native_article_context_captured_at === "string"
    ? tokenSources.native_article_context_captured_at
    : "";
  const monitorResults = batchItemsFromResponse(batchResult);
  const recentHistory = historyItemsFromResponse(historyQuery.data);
  const tokenReadyLabel = useMemo(() => {
    if (tokenQuery.isLoading) return "读取中";
    if (tokenQuery.isError) return "状态不可用";
    if (!tokenStatus?.available) return "未捕获";
    if (tokenStatus.expired) return "已过期";
    if (!tokenStatus.complete) return "部分可用";
    // Older API responses do not have `auxiliary`; keep their legacy label.
    if (!tokenStatus.auxiliary) return "完整可用";
    return tokenStatus.auxiliary.appmsg_token?.present ? "完整可用" : "核心完整";
  }, [tokenQuery.isError, tokenQuery.isLoading, tokenStatus]);

  function validateUrl(): boolean {
    const value = url.trim();
    if (!value) {
      setUrlError("请粘贴公众号文章 URL");
      return false;
    }
    if (!isArticleUrl(value)) {
      setUrlError("仅支持 mp.weixin.qq.com 文章链接");
      return false;
    }
    setUrlError("");
    return true;
  }

  function collectMetrics(event: FormEvent) {
    event.preventDefault();
    if (!validateUrl()) return;
    setExportResult(undefined);
    metricsMutation.mutate(url.trim());
  }

  function importTokens(event: FormEvent) {
    event.preventDefault();
    const values = Object.fromEntries(Object.entries(tokenForm).filter(([, value]) => Boolean(value?.trim()))) as MPTokenInput;
    if (!Object.keys(values).length) {
      setTokenMessage("");
      setTokenError("至少填写一个 Token 字段");
      return;
    }
    tokenMutation.mutate({ values, source: tokenSource.trim() || "manual-import" });
  }

  function toggleFormat(value: MPExportFormat) {
    setFormats((current) => current.includes(value) ? current.filter((item) => item !== value) : [...current, value]);
  }

  function exportArticle(event: FormEvent) {
    event.preventDefault();
    if (!validateUrl()) return;
    if (!formats.length) {
      setExportError("至少选择一种导出格式");
      return;
    }
    exportMutation.mutate();
  }

  function collectCompetitors(event: FormEvent) {
    event.preventDefault();
    const urls = monitorUrlsFromText(monitorUrls);
    if (!urls.length) {
      setBatchError("请至少输入一条公众号文章 URL");
      return;
    }
    if (urls.length > 50) {
      setBatchError("一次最多监控 50 条文章，请拆分后再试");
      return;
    }
    setBatchError("");
    batchMutation.mutate(urls);
  }

  return (
    <div className="page mp-page">
      <header className="mp-heading">
        <div>
          <div className="mp-eyebrow"><BarChart3 size={15} />官方账号工具</div>
          <h1>公众号工作台</h1>
          <p>采集真实互动指标、维持动态会话，并把文章无损归档到本地。</p>
        </div>
        <span className={`mp-status-chip ${tokenStatus?.complete && !tokenStatus.expired ? "ready" : "pending"}`} aria-live="polite">
          <i aria-hidden="true" /> Token {tokenReadyLabel}
        </span>
      </header>

      <section className="card mp-url-card" aria-labelledby="mp-url-title">
        <div className="mp-section-heading">
          <div><span className="mp-step">01</span><div><h2 id="mp-url-title">输入文章链接</h2><p>使用微信公众平台文章地址，服务端会自动读取可用的动态凭据。</p></div></div>
          <Link2 size={19} aria-hidden="true" />
        </div>
        <form className="mp-url-form" onSubmit={collectMetrics} noValidate>
          <label htmlFor="mp-article-url">公众号文章 URL</label>
          <div className="mp-url-control">
            <Link2 size={18} aria-hidden="true" />
            <input
              id="mp-article-url"
              type="url"
              value={url}
              onChange={(event) => { setUrl(event.target.value); if (urlError) setUrlError(""); }}
              placeholder="https://mp.weixin.qq.com/s/..."
              autoComplete="url"
              aria-invalid={Boolean(urlError)}
              aria-describedby={urlError ? "mp-url-error" : "mp-url-help"}
            />
            <button className="green-button" type="submit" disabled={metricsMutation.isPending}>
              {metricsMutation.isPending ? <LoaderCircle className="spin" size={16} /> : <BarChart3 size={16} />}
              {metricsMutation.isPending ? "采集中" : "采集指标"}
            </button>
          </div>
          <small id="mp-url-help">仅在本机处理请求；文章 URL 中的动态参数不会写入导出文件。</small>
          {urlError && <p className="mp-form-error" id="mp-url-error" role="alert"><AlertTriangle size={14} />{urlError}</p>}
          {metricsMutation.isError && <p className="mp-form-error" role="alert"><AlertTriangle size={14} />{messageFromError(metricsMutation.error)}</p>}
        </form>
      </section>

      <section className="card mp-monitor-card" aria-labelledby="mp-monitor-title">
        <div className="mp-section-heading">
          <div><span className="mp-step">02</span><div><h2 id="mp-monitor-title">竞品监控</h2><p>每行一条文章链接，按输入顺序采集并保留最近快照。</p></div></div>
          <ListOrdered size={19} aria-hidden="true" />
        </div>
        <div className="mp-monitor-grid">
          <div className="mp-monitor-current">
            <form className="mp-monitor-form" onSubmit={collectCompetitors}>
              <label htmlFor="mp-monitor-urls">竞品文章 URL（每行一条）</label>
              <textarea
                id="mp-monitor-urls"
                value={monitorUrls}
                onChange={(event) => { setMonitorUrls(event.target.value); if (batchError) setBatchError(""); }}
                placeholder={'https://mp.weixin.qq.com/s/article-one\nhttps://mp.weixin.qq.com/s/article-two'}
                rows={4}
                spellCheck={false}
                aria-describedby="mp-monitor-help"
              />
              <div className="mp-monitor-form-footer"><small id="mp-monitor-help">支持最多 50 条；空行会自动忽略，错误会保留在对应行。</small><button className="green-button" type="submit" disabled={batchMutation.isPending}>{batchMutation.isPending ? <LoaderCircle className="spin" size={15} /> : <ListOrdered size={15} />}{batchMutation.isPending ? "监控中" : "开始批量采集"}</button></div>
              {batchError && <p className="mp-form-error" role="alert"><AlertTriangle size={14} />{batchError}</p>}
            </form>
            {batchMutation.isPending && <div className="mp-monitor-loading" role="status"><LoaderCircle className="spin" size={17} />正在按序整理竞品快照…</div>}
            {batchResult ? (
              <div className="mp-batch-results" aria-live="polite">
                <div className="mp-batch-summary"><strong>本次结果</strong><span className="success">成功 {batchResult.succeeded ?? monitorResults.filter((item) => item.ok).length}</span><span className="failure">失败 {batchResult.failed ?? monitorResults.filter((item) => !item.ok).length}</span><span>共 {batchResult.total ?? monitorResults.length} 条</span></div>
                <div className="mp-batch-table-wrap"><table className="mp-batch-table"><caption>竞品文章批量采集结果</caption><thead><tr><th scope="col">序</th><th scope="col">文章</th><th scope="col">阅读</th><th scope="col">在看</th><th scope="col">点赞</th><th scope="col">评论</th><th scope="col">错误</th></tr></thead><tbody>{monitorResults.map((item) => { const data = batchItemData(item); const error = item.error?.detail; return <tr key={`${item.index}-${item.url}`} className={item.ok ? "ok" : "failed"}><td>{item.index + 1}</td><td><strong>{safeText(data.title, "未识别标题")}</strong><code title={item.url}>{item.url}</code></td><td>{formatNumber(data.read_count ?? data.display_read_count)}</td><td>{formatNumber(data.wow_count)}</td><td>{formatNumber(data.like_count)}</td><td>{formatNumber(data.comment_count ?? data.comments?.length)}</td><td>{error ? <span className="mp-batch-error" title={error}><AlertTriangle size={13} />{error}</span> : <span className="mp-batch-ok"><Check size={13} />已完成</span>}</td></tr>; })}</tbody></table></div>
              </div>
            ) : <div className="mp-monitor-empty"><ListOrdered size={21} /><span>输入多条链接后，这里会按序显示竞品指标。</span></div>}
          </div>
          <aside className="mp-history-panel" aria-labelledby="mp-history-title">
            <div className="mp-history-heading"><div><History size={16} /><h3 id="mp-history-title">最近快照</h3></div><button className="mp-icon-button" type="button" onClick={() => historyQuery.refetch()} disabled={historyQuery.isFetching} aria-label="刷新竞品快照" title="刷新竞品快照"><RefreshCw size={14} className={historyQuery.isFetching ? "spin" : ""} /></button></div>
            {historyQuery.isError ? <p className="mp-form-error mp-card-error" role="alert"><AlertTriangle size={14} />{messageFromError(historyQuery.error)}</p> : recentHistory.length ? <ul className="mp-history-list">{recentHistory.map((item, index) => <li key={`${item.url}-${item.collected_at || index}`}><div><strong>{safeText(item.title, "未识别标题")}</strong><small>{item.collected_at ? formatDateTime(item.collected_at) : "时间未知"}</small></div><span>阅 {formatNumber(item.read_count ?? item.display_read_count)}</span><span>在看 {formatNumber(item.wow_count)}</span></li>)}</ul> : <div className="mp-history-empty"><History size={20} /><span>暂无历史快照</span><small>批量采集成功后会自动记录。</small></div>}
          </aside>
        </div>
      </section>

      <div className="mp-main-grid">
        <section className="card mp-metrics-card" aria-labelledby="mp-metrics-title">
          <div className="card-header"><div className="card-title" id="mp-metrics-title"><Eye size={17} />文章指标</div>{metrics?.collected_at && <time className="mp-muted">采集于 {formatDateTime(metrics.collected_at)}</time>}</div>
          {metrics ? (
            <>
              <div className="mp-article-meta">
                <div><h2>{safeText(metrics.title, "未识别标题")}</h2><p><UserRound size={13} />{safeText(metrics.author, "未知作者")} <span>·</span> {metrics.publish_time ? formatDateTime(metrics.publish_time * 1000, true) : "发布时间未知"}</p></div>
                {metrics.url && <a href={metrics.url} className="mp-source-link" target="_blank" rel="noreferrer">查看原文 <Link2 size={13} /></a>}
              </div>
              <div className="mp-metric-grid">
                <MetricTile icon={Eye} label="真实阅读量" value={metrics.read_count ?? metrics.display_read_count} />
                <MetricTile icon={BarChart3} label="在看数" value={metrics.wow_count} tone="blue" />
                <MetricTile icon={Heart} label="点赞数" value={metrics.like_count} tone="orange" />
                <MetricTile icon={MessageSquare} label="评论数" value={metrics.comment_count ?? metrics.comments?.length} tone="violet" />
              </div>
              <div className="mp-evidence-row"><span><ShieldCheck size={14} />来源：{metrics.sources?.join("、") || "本地响应"}</span><span>指标依据：{Object.values(metrics.metric_basis ?? {}).join("、") || "服务端解析"}</span></div>
              {metrics.warnings?.length ? <div className="mp-warning"><AlertTriangle size={15} /><span>{metrics.warnings.join("；")}</span></div> : null}
              <div className="mp-comments" aria-labelledby="mp-comments-title">
                <div className="mp-subheading"><h3 id="mp-comments-title">最新评论</h3><span>{formatNumber(metrics.comments?.length ?? 0)} 条已返回</span></div>
                {metrics.comments?.length ? <ul>{metrics.comments.slice(0, 5).map((comment, index) => <li key={comment.content_id || `${comment.nickname}-${index}`}><span className="mp-comment-avatar">{[...(comment.nickname || "匿")].slice(0, 1)}</span><div><strong>{safeText(comment.nickname, "匿名用户")}</strong><p>{comment.content}</p></div><small>{comment.like_count ? `${formatNumber(comment.like_count)} 赞` : ""}</small></li>)}</ul> : <p className="mp-empty-inline">暂无评论内容，可能是文章关闭了评论或当前会话未返回明细。</p>}
              </div>
            </>
          ) : <div className="mp-empty-panel"><Eye size={28} /><strong>尚未采集文章</strong><p>粘贴链接后，四项核心指标和最新评论会显示在这里。</p></div>}
        </section>

        <aside className="card mp-token-card" aria-labelledby="mp-token-title">
          <div className="card-header"><div className="card-title" id="mp-token-title"><KeyRound size={17} />动态 Token</div><button className="mp-icon-button" type="button" onClick={() => tokenQuery.refetch()} disabled={tokenQuery.isFetching} aria-label="刷新 Token 状态" title="刷新 Token 状态"><RefreshCw size={15} className={tokenQuery.isFetching ? "spin" : ""} /></button></div>
          <div className={`mp-token-summary ${tokenStatus?.complete && !tokenStatus.expired ? "ready" : "pending"}`}><span className="mp-token-summary-icon"><ShieldCheck size={18} /></span><div><strong>{tokenReadyLabel}</strong><p>{tokenStatus?.expires_at ? `有效期至 ${formatDateTime(tokenStatus.expires_at)}` : "等待浏览器请求或手动导入"}</p></div></div>
          {tokenQuery.isError ? <p className="mp-form-error mp-card-error" role="alert"><AlertTriangle size={14} />{messageFromError(tokenQuery.error)}</p> : <ul className="mp-token-list">{TOKEN_FIELDS.map(({ name }) => { const field = tokenFieldStatus(tokenStatus, name); return <TokenState key={name} name={name} present={Boolean(field?.present)} value={field?.value} expired={Boolean(tokenStatus?.expired)} />; })}</ul>}
          <div className="mp-token-source"><span>最近来源</span><strong>{safeText(tokenStatus?.source, "暂无")}</strong>{tokenStatus?.captured_at && <time>{formatDateTime(tokenStatus.captured_at)}</time>}</div>
          <div className="mp-token-source"><span>短链身份</span><strong>{nativeContextAvailable ? `已捕获 ${nativeContextCount} 篇` : "待捕获"}</strong>{nativeContextCapturedAt && <time>{formatDateTime(nativeContextCapturedAt)}</time>}</div>
          <form className="mp-token-form" onSubmit={importTokens}>
            <div className="mp-subheading"><h3>手动导入</h3><span>可只填变化字段</span></div>
            {TOKEN_FIELDS.map(({ name, label, hint }) => <label key={name} htmlFor={`mp-token-${name}`}><span>{label}<small>{hint}</small></span><input id={`mp-token-${name}`} type="password" value={tokenForm[name] || ""} onChange={(event) => setTokenForm((current) => ({ ...current, [name]: event.target.value }))} autoComplete="off" spellCheck={false} /></label>)}
            <label htmlFor="mp-token-source"><span>来源<small>用于排查采集链路</small></span><input id="mp-token-source" type="text" value={tokenSource} onChange={(event) => setTokenSource(event.target.value)} maxLength={80} /></label>
            {tokenError && <p className="mp-form-error" role="alert"><AlertTriangle size={14} />{tokenError}</p>}
            {tokenMessage && <p className="mp-form-success" role="status"><Check size={14} />{tokenMessage}</p>}
            <div className="mp-token-actions"><button className="green-button" type="submit" disabled={tokenMutation.isPending}>{tokenMutation.isPending ? <LoaderCircle className="spin" size={15} /> : <ClipboardPaste size={15} />}{tokenMutation.isPending ? "导入中" : "安全导入"}</button><button className="outline-button danger" type="button" onClick={() => clearTokenMutation.mutate()} disabled={clearTokenMutation.isPending || !tokenStatus?.available}><Trash2 size={15} />清除</button></div>
          </form>
        </aside>
      </div>

      <section className="card mp-export-card" aria-labelledby="mp-export-title">
        <div className="mp-section-heading"><div><span className="mp-step">03</span><div><h2 id="mp-export-title">无损备份</h2><p>下载文章正文、图片、视频和导出清单；动态凭据不会出现在文件里。</p></div></div><Download size={19} aria-hidden="true" /></div>
        <form className="mp-export-form" onSubmit={exportArticle}>
          <fieldset><legend>选择导出格式</legend><div className="mp-format-grid">{EXPORT_OPTIONS.map(({ value, label, detail, icon: Icon }) => { const selected = formats.includes(value); return <label className={`mp-format-option ${selected ? "selected" : ""}`} key={value}><input type="checkbox" checked={selected} onChange={() => toggleFormat(value)} /><span className="mp-format-icon"><Icon size={18} /></span><span><strong>{label}</strong><small>{detail}</small></span><span className="mp-check" aria-hidden="true">{selected ? <Check size={13} /> : null}</span></label>; })}</div></fieldset>
          <div className="mp-export-actions"><button className="green-button" type="submit" disabled={exportMutation.isPending}>{exportMutation.isPending ? <LoaderCircle className="spin" size={16} /> : <Download size={16} />}{exportMutation.isPending ? "导出中" : "开始备份"}</button><span>将使用上方文章链接</span></div>
          {exportError && <p className="mp-form-error" role="alert"><AlertTriangle size={14} />{exportError}</p>}
        </form>
        {exportResult && <div className="mp-export-result" role="status"><div className="mp-export-result-heading"><div><Check size={17} /><strong>{safeText(exportResult.title, "文章备份已生成")}</strong><small>{exportResult.pdf_backend ? `PDF 引擎：${exportResult.pdf_backend}` : "资源已本地化"}</small></div><a className="green-button" href={assetUrl(exportResult.download_url)} download><Download size={15} />下载完整 ZIP</a></div><div className="mp-download-list">{Object.entries(exportResult.files).filter(([name]) => name !== "manifest").map(([name, href]) => <a key={name} href={assetUrl(href)} download><span>{name === "markdown" ? "Markdown" : name.toUpperCase()}</span><Download size={14} /></a>)}</div>{exportResult.warnings?.length ? <p className="mp-warning"><AlertTriangle size={14} />{exportResult.warnings.join("；")}</p> : null}</div>}
      </section>
    </div>
  );
}
