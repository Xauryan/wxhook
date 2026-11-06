import { FormEvent, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileText, Search } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Avatar, Card, EmptyState, ErrorState, LoadingState, Pill } from "../components/ui";
import { api } from "../lib/api";
import { formatDateTime, safeText } from "../lib/format";

export function SearchPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const scope = params.get("scope") ?? "all";
  const [input, setInput] = useState(q);
  useEffect(() => setInput(q), [q]);
  const query = useQuery({ queryKey: ["search", q, scope], queryFn: ({ signal }) => api.search(q, scope, signal), enabled: Boolean(q.trim()) });
  function submit(event: FormEvent) { event.preventDefault(); const next = new URLSearchParams(params); if (input.trim()) next.set("q", input.trim()); else next.delete("q"); setParams(next); }
  return <div className="page search-page"><Card className="search-hero"><h1>本地全文搜索</h1><p>联系人、会话、消息与收藏数据仅在本机查询。</p><form onSubmit={submit}><Search /><input autoFocus value={input} onChange={(event) => setInput(event.target.value)} placeholder="输入中文、联系人或空格拼音" /><button type="submit">搜索</button></form><div className="scope-tabs">{[["all", "全部"], ["messages", "消息"], ["contacts", "联系人"], ["favorites", "收藏"]].map(([value, label]) => <button type="button" className={scope === value ? "active" : ""} onClick={() => { const next = new URLSearchParams(params); next.set("scope", value); setParams(next); }} key={value}>{label}</button>)}</div></Card>{!q ? <EmptyState title="输入关键词开始搜索" detail="支持中文与联系人空格拼音查询。" /> : query.isLoading ? <LoadingState label="正在查询本地 FTS 索引" /> : query.isError ? <ErrorState error={query.error} retry={() => query.refetch()} /> : <Card title={`“${q}” 的搜索结果`} action={<Pill tone={query.data?.fts_used ? "green" : "neutral"}>{query.data?.fts_used ? "FTS" : "普通查询"}</Pill>}>{query.data?.items?.length ? <div className="search-results">{query.data.items.map((item, index) => <button type="button" key={safeText(item.id, String(index))} onClick={() => item.session_username && navigate(`/conversations?session=${encodeURIComponent(String(item.session_username))}&q=${encodeURIComponent(q)}`)}><Avatar src={safeText(item.avatar_url, "")} name={safeText(item.title || item.session_name, "搜索结果")} size={38} /><span><b>{safeText(item.title || item.session_name, "匹配内容")}</b><p>{safeText(item.snippet, "当前结果没有文本片段")}</p><small><FileText size={13} />{safeText(item.kind, "记录")} · {formatDateTime(item.create_time)}</small></span></button>)}</div> : <EmptyState title="没有匹配结果" />}{query.data?.errors?.length ? <div className="search-errors">{query.data.errors.join("；")}</div> : null}</Card>}</div>;
}
