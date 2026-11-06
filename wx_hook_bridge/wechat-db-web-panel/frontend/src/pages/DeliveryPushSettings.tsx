import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { API_BASE } from "../lib/api";
import { Card } from "../components/ui";
import { MessageCircle } from "lucide-react";
import { CALL_EXAMPLE, previewCall, previewOverview, type PreviewCall, type PreviewOverview } from "./delivery-preview";
import { DeliveryMentionSettings } from './DeliveryMentionSettings';

type Group = { room_id: string; name: string };
type Config = { enabled: boolean; target_groups: Group[]; activated_at: string | null; scheduled_enabled?: boolean; reminder_enabled?: boolean; result_notifications_enabled?: boolean; failure_notifications_enabled?: boolean; stage_times?: string[]; first_call_minutes?: number };
type Status = { config: Config; call_quotes?: { event_key: string; room_id: string; status: string; error: string | null }[]; feedback_wait?: { minutes: number; items: { case_id: number; order_id: number; deadline_at: string; status: string; error: string | null }[] }; stages?: { report_key: string; cutoff: string; status: string; media_type?: string }[]; runtime: { running: boolean; last_poll_at: string | null; last_error: string | null }; recent: { event_key: string; room_id?: string; room_name: string; status: string; error: string | null; media_type?: string; text_status?: string | null }[] };

async function request<T>(path: string, payload?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}/delivery-management${path}`, payload === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "配送推送请求失败");
  return data;
}

export function DeliveryPushSettings({ day, call, overview }: { day: string; call?: PreviewCall; overview?: PreviewOverview }) {
  const status = useQuery({ queryKey: ["delivery-push", "status"], queryFn: () => request<Status>("/notifications"), refetchInterval: 10000, retry: false });
  const groups = useQuery({ queryKey: ["delivery-push", "groups"], queryFn: () => request<{ items: Group[] }>("/groups"), retry: false });
  const [draft, setDraft] = useState<Config | null>(null);
  const [search, setSearch] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [stageTimes, setStageTimes] = useState("12:00,16:00,18:30,21:00");
  const [previewMode, setPreviewMode] = useState<"call" | "overview">("call");
  const [previewGroup, setPreviewGroup] = useState("");
  const [stagePreview, setStagePreview] = useState<{ cutoff: string; message: string; media_type?: string; report_key?: string } | null>(null);
  const [callPreview, setCallPreview] = useState<{ message: string; image_data_url: string; validity_note?: string } | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const previewGeneration = useRef(0);
  useEffect(() => { previewGeneration.current++; setCallPreview(null); setPreviewBusy(false); }, [day, call?.id]);
  const requestId = useRef<{ day: string; id: string } | null>(null);
  useEffect(() => { if (status.data?.config && !dirty) { setDraft(status.data.config); setStageTimes((status.data.config.stage_times ?? ["12:00","16:00","18:30","21:00"]).join(",")); } }, [status.data, dirty]);
  const quoteFor = (row: Status['recent'][number]) => status.data?.call_quotes?.find(q => q.event_key === row.event_key && q.room_id === row.room_id);
  const quoteLabels: Record<string, string> = { waiting_mentions: '请核对所选 @ 人员，文字尚未发送', waiting_source: '等待当前账号消息源', waiting_reference: '等待原文字消息编号，仅暂缓图片', awaiting_evidence: '引用图片桥接已接受，等待入库证据', local_verified: '已查库确认统计图片引用对应文字', image_unavailable: '统计图片暂不可用，未发送', rejected: '引用被拒绝，不自动重试', uncertain: '结果待核对，不重发' };
  const known = new Map([...(draft?.target_groups ?? []), ...(groups.data?.items ?? [])].map(group => [group.room_id, group]));
  const visible = [...known.values()].filter(group => `${group.name} ${group.room_id}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  function toggle(group: Group) {
    if (!draft) return;
    setDraft({ ...draft, target_groups: draft.target_groups.some(item => item.room_id === group.room_id) ? draft.target_groups.filter(item => item.room_id !== group.room_id) : [...draft.target_groups, group] });
    setDirty(true); setMessage("");
  }
  async function save() {
    if (!draft) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await request<Status>("/notifications/config", { enabled: draft.enabled, target_groups: draft.target_groups,
        scheduled_enabled: !!draft.scheduled_enabled, reminder_enabled: !!draft.reminder_enabled, result_notifications_enabled: !!draft.result_notifications_enabled,
        failure_notifications_enabled: !!draft.failure_notifications_enabled,
        stage_times: stageTimes.split(/[,，\s]+/).filter(Boolean), first_call_minutes: draft.first_call_minutes ?? 60 });
      setDraft(result.config); requestId.current = null;
      await status.refetch(); setDirty(false);
      setMessage("设置已保存");
    } catch (error) { setError(error instanceof Error ? error.message : "保存失败"); }
    finally { setBusy(false); }
  }
  async function sendOverview() {
    if (!draft?.target_groups.length || dirty) return;
    if (!window.confirm(`将 ${day} 的配送统计发送到以下微信群：\n${draft.target_groups.map(g => g.name).join("\n")}\n确认发送？`)) return;
    setBusy(true); setError(""); setMessage("");
    if (!requestId.current || requestId.current.day !== day) requestId.current = { day, id: crypto.randomUUID() };
    try {
      await request<Status>("/notifications/send-overview", { day, request_id: requestId.current.id });
      await status.refetch();
      setMessage("已加入发送队列");
    } catch (error) { setError(error instanceof Error ? error.message : "推送失败"); }
    finally { setBusy(false); }
  }
  const selectedGroups = draft?.target_groups ?? [];
  const shownGroup = selectedGroups.find(group => group.room_id === previewGroup) ?? selectedGroups[0];
  const previewText = stagePreview?.message ?? (previewMode === "call" ? callPreview?.message ?? previewCall(call ?? { ...CALL_EXAMPLE, call_time: `${day}T10:23:00+08:00` }) : overview?.items.length ? previewOverview(day, overview) : "暂无配送统计");
  async function showCallReport() {
    if (!call?.id) return;
    const generation = ++previewGeneration.current;
    setPreviewBusy(true); setError("");
    try {
      const result = await request<{ message: string; image_data_url: string; validity_note?: string }>(`/calls/${call.id}/report-preview?day=${encodeURIComponent(day)}`);
      if (generation === previewGeneration.current) { setCallPreview(result); setStagePreview(null); setPreviewMode("call"); }
    } catch (error) { if (generation === previewGeneration.current) setError(error instanceof Error ? error.message : "图文预览失败"); }
    finally { if (generation === previewGeneration.current) setPreviewBusy(false); }
  }
  const active = status.data?.config;
  const pushActive = active && (active.enabled || active.scheduled_enabled || active.reminder_enabled || active.result_notifications_enabled || active.failure_notifications_enabled);
  async function showStage(key: string) {
    setError("");
    try { const result = await request<{ cutoff: string; message: string | null; media_type?: string; report_key: string }>(`/notifications/stages/${encodeURIComponent(key)}`); if (result.message || result.media_type === 'image') setStagePreview({ ...result, message: result.message || '' }); }
    catch (error) { setError(error instanceof Error ? error.message : "读取报表失败"); }
  }
  async function retryImage(key: string) {
    if (!window.confirm('仅重试图片生成失败、尚未发送的报表。已经提交或结果不明的消息不会重发。确认继续？')) return;
    setBusy(true); setError('');
    try { await request(`/notifications/${key.startsWith('delivery.call:') ? 'calls' : 'stages'}/${encodeURIComponent(key)}/retry-render`, {}); await status.refetch(); }
    catch (error) { setError(error instanceof Error ? error.message : '重试失败'); }
    finally { setBusy(false); }
  }
  return <div className="delivery-push-layout"><Card title="配送群推送设置" className="delivery-settings-card" action={<span className="delivery-status-badge">{pushActive ? "自动推送开" : "自动推送关"}</span>}>
    {status.isPending && <p role="status">正在读取推送配置…</p>}
    {status.error && <p role="alert">推送配置读取失败：{status.error.message}</p>}
    {draft && <>
      <label className="delivery-push-toggle"><input type="checkbox" checked={draft.enabled} disabled={busy} onChange={event => { setDraft({ ...draft, enabled: event.target.checked }); setDirty(true); setMessage(""); }} />自动推送新增配送电话（文字＋统计图）</label>
      <p className="delivery-help">配送通话统计图片原生引用对应的文字通知；真实消息编号未就绪则只暂缓图片，不重复文字、不退回普通图片。</p>
      <label className="delivery-push-toggle"><input type="checkbox" checked={!!draft.failure_notifications_enabled} disabled={busy} onChange={event => { setDraft({ ...draft, failure_notifications_enabled: event.target.checked }); setDirty(true); }} />新发生的无法配送反馈及录音长图（不补发启用前历史）</label>
      <p className="delivery-help">反馈等待录音最多 {status.data?.feedback_wait?.minutes ?? 10} 分钟：两侧长图就绪可提前发，到期至少一侧就绪才发送文字和长图；完全无就绪资料则继续等待，不发空反馈。只使用已筛选的外呼成单和本次无法配送沟通，长图原生引用本次文字通知，消息编号未就绪时暂缓；后就绪的另一侧只补图，已发或回执不明的消息不重发。</p>
      <div className="delivery-group-toolbar"><input aria-label="搜索目标微信群" type="search" placeholder="搜索微信群" value={search} onChange={event => setSearch(event.target.value)} /><button type="button" disabled={groups.isFetching || busy} onClick={() => void groups.refetch()}>刷新群列表</button></div>
      {groups.isPending && <p role="status">正在读取微信群…</p>}
      {groups.error && <p role="alert">{groups.error.message}</p>}
      <div className="delivery-group-list" aria-label="配送目标微信群">
        {visible.map(group => <label className="delivery-group-choice" key={group.room_id} title={group.room_id}><input type="checkbox" checked={draft.target_groups.some(item => item.room_id === group.room_id)} disabled={busy} onChange={() => toggle(group)} /><span>{group.name}</span></label>)}
        {!groups.isPending && !groups.error && !visible.length && <p>没有匹配的群</p>}
      </div>
      <div className="delivery-selection-status">已选 {draft.target_groups.length} 个群{dirty ? " · 未保存" : ""}</div>
      <div className="delivery-controls delivery-save-actions"><button className="delivery-primary" type="button" aria-label="保存配送推送设置" disabled={busy || (draft.enabled && !draft.target_groups.length)} onClick={() => void save()}>{busy ? "处理中…" : "保存设置"}</button><button type="button" aria-label={`推送 ${day} 配送统计`} disabled={busy || dirty || !draft.target_groups.length || !status.data?.runtime.running} onClick={() => void sendOverview()}>推送统计</button></div>
      {status.data && !status.data.runtime.running && <p role="alert">发送服务未运行</p>}
      <DeliveryMentionSettings rooms={status.data?.config.target_groups ?? []} disabled={busy || dirty} />
      <details className="delivery-help"><summary>定时与提醒</summary><fieldset disabled={busy} className="delivery-policy-grid">
        <label><input type="checkbox" checked={!!draft.scheduled_enabled} onChange={event => { setDraft({ ...draft, scheduled_enabled: event.target.checked }); setDirty(true); }} />定时阶段报表（图片）</label>
        <label>报表时点（北京时间）<input value={stageTimes} onChange={event => { setStageTimes(event.target.value); setDirty(true); }} placeholder="12:00,16:00,18:30,21:00" /></label>
        <label><input type="checkbox" checked={!!draft.reminder_enabled} onChange={event => { setDraft({ ...draft, reminder_enabled: event.target.checked }); setDirty(true); }} />超时待办提醒</label>
        <label>首拨期限（分钟）<input type="number" min={5} max={1440} value={draft.first_call_minutes ?? 60} onChange={event => { setDraft({ ...draft, first_call_minutes: Number(event.target.value) }); setDirty(true); }} /></label>
        <label><input type="checkbox" checked={!!draft.result_notifications_enabled} onChange={event => { setDraft({ ...draft, result_notifications_enabled: event.target.checked }); setDirty(true); }} />AI 复盘完成通知</label>
      </fieldset></details>
      <details className="delivery-help"><summary>推送规则</summary><p>只推送启用后的新事件，不补发历史。定时报表按图片发送，只统计当前启用配送员；超过 30 分钟未生成快照记为错过。图片生成失败不会改发文字，旧文字报表不补发。录音与 AI 结果需人工复核。</p></details>
    </>}
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
    {status.data?.runtime.last_error && <p role="alert">{status.data.runtime.last_error}</p>}
    {!!status.data?.feedback_wait?.items.length && <details className="delivery-help"><summary>等待录音的反馈（{status.data.feedback_wait.items.length}）</summary><div className="delivery-table"><table><thead><tr><th>订单</th><th>双侧等待截止</th><th>状态</th></tr></thead><tbody>{status.data.feedback_wait.items.map(row => <tr key={row.case_id}><td>#{row.order_id}</td><td>{new Date(row.deadline_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}</td><td>{({ waiting_mentions: '请核对所选 @ 人员，文字尚未发送', waiting_reference: '等待原通知消息编号，暂缓引用图片', awaiting_quote_evidence: '等待图片引用入库确认，不重发', waiting_window: '等待另一侧录音', no_materials: '暂无就绪资料，未发送', image_not_ready: '长图未就绪，未发送', error: '处理异常，未发送', blocked: '回执待核对，不自动重发' } as Record<string, string>)[row.status] || row.status}{row.error && <p>{row.error}</p>}</td></tr>)}</tbody></table></div></details>}
    {!!status.data?.stages?.length && <details className="delivery-help"><summary>阶段报表记录</summary>{status.data.stages.map(stage => <p key={stage.report_key}>{stage.cutoff} · {stage.status === "ready" ? <button type="button" onClick={() => void showStage(stage.report_key)}>预览阶段报表</button> : "错过时点"}</p>)}</details>}
    {!!status.data?.recent.length && <details className="delivery-help"><summary>最近发送记录</summary><div className="delivery-table"><table><thead><tr><th>目标群</th><th>通知</th><th>发送回执</th></tr></thead><tbody>{status.data.recent.map(row => <tr key={`${row.event_key}:${row.room_name}`}><td>{row.room_name}</td><td>{row.event_key.startsWith("delivery.call:") ? "配送电话" : row.event_key.startsWith("delivery.stage:") ? "阶段报表" : row.event_key.startsWith("delivery.analysis:") ? "AI 复盘" : row.event_key.startsWith("delivery.overview:") ? "配送统计" : "待办提醒"}</td><td>{({ queued: "待发送", quote_waiting: "等待引用条件就绪", rejected: "引用被拒绝，不降级、不重试", sending: "发送中", sent: "桥接已接受", render_failed: "图片生成失败，未发送", uncertain: "结果待核对，不自动重发", cancelled: "配置变更，已取消" } as Record<string, string>)[row.status] || row.status}{row.error && <p>{row.error}</p>}{quoteFor(row) && <p>{quoteLabels[quoteFor(row)!.status] || quoteFor(row)!.status}</p>}{row.status === "render_failed" && <button disabled={busy} onClick={() => void retryImage(row.event_key)}>重试图片生成</button>}</td></tr>)}</tbody></table></div></details>}
  </Card><Card title="微信群预览" className="delivery-preview-card" action={<span className="delivery-preview-label">{stagePreview ? "历史阶段快照" : previewMode === "call" ? call ? "当前页通话" : "示例消息" : "所选日期快照"}</span>}>
    <div className="delivery-preview-tabs" aria-label="消息预览类型"><button type="button" aria-pressed={!stagePreview && previewMode === "call"} onClick={() => { setPreviewMode("call"); setStagePreview(null); }}>电话通知</button><button type="button" aria-pressed={!stagePreview && previewMode === "overview"} onClick={() => { setPreviewMode("overview"); setStagePreview(null); }}>配送统计</button><button type="button" disabled={!call?.id || previewBusy} onClick={() => void showCallReport()}>{previewBusy ? "生成预览中…" : "预览电话图文（不发送）"}</button></div>
    <div className="delivery-wechat"><div className="delivery-wechat-bar">{stagePreview ? <span>阶段报表 · {stagePreview.cutoff}</span> : selectedGroups.length ? <select aria-label="预览微信群" value={shownGroup?.room_id ?? ""} onChange={event => setPreviewGroup(event.target.value)}>{selectedGroups.map(group => <option key={group.room_id} value={group.room_id}>{group.name}</option>)}</select> : <span>配送通知群</span>}<span>···</span></div>
      <div className="delivery-wechat-body" style={!stagePreview && previewMode === "call" && callPreview ? { height: "auto", overflowY: "visible" } : undefined}><div className="delivery-preview-time">{stagePreview && stagePreview.media_type !== "image" ? "历史文字快照 · 保留原文，不会重算或重新发送" : "消息效果预览 · 不会发送"}</div><div className="delivery-message"><span className="delivery-message-avatar"><MessageCircle size={20} /></span><div className="delivery-message-content"><small>配送助手</small>{stagePreview?.media_type === "image" && stagePreview.report_key ? <img alt="配送阶段报表图片" src={`${API_BASE}/delivery-management/notifications/stages/${encodeURIComponent(stagePreview.report_key)}/image`} style={{ display: "block", maxWidth: "100%", height: "auto" }} onError={() => setError("报表图片加载失败，请查看发送记录；不会退回文字发送")} /> : <pre aria-label="微信群消息内容">{previewText}</pre>}{!stagePreview && previewMode === 'call' && callPreview && <img alt="配送通话月度统计图片" src={callPreview.image_data_url} style={{ display: 'block', maxWidth: '100%', height: 'auto', marginTop: 12 }} />}</div></div></div>
    </div>
    {!stagePreview && previewMode === 'call' && callPreview && <p className="delivery-help">{callPreview.validity_note}。此处为当前只读快照，不改历史通知。</p>}
  </Card></div>;
}
