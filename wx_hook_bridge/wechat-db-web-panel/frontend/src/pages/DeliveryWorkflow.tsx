import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { API_BASE } from "../lib/api";
import { Card } from "../components/ui";
import { DeliveryDialogues } from './DeliveryDialogues';

export async function workflowRequest<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}/delivery-management${path}`, body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "操作失败，请刷新后重试");
  return value;
}

type Ref = { role: string; text: string; begin_ms: number | null; end_ms: number | null; speaker_id?: number };
type Report = { validation_status?: string; omitted_findings?: number; summary: string; findings: { issue: string; kind: string; refs: string[] }[]; evidence_gaps: string[]; next_actions: string[]; references: Record<string, Ref>; recoverability: string };
type Call = { id: number; user_name: string; role: string; call_time: string; recording_available: boolean; full_text: string | null; delivery_transcript?: { status: string; full_text: string | null; error: string | null } | null };
type Assignment = { id: number; staff_name: string | null; valid_from: string; valid_to: string | null; baseline: boolean };
type History = { assignments: Assignment[]; events: { id: number; kind: string; created_at: string }[] };
type CaseRow = { id: number; order_id: number; customer_name?: string; status: string; review_status: string; owner_user_id: number | null; owner_name: string | null; due_at: string | null; callback_at: string | null; resolution: string | null; version: number; analysis_status: string; analysis_error: string | null; analysis: Report | null; reported_reason: string | null };
type CaseDetail = CaseRow & { candidates: { items: Call[]; has_more: boolean }; selected_calls: { id: number; role: string; purpose?: string }[] | null; material_scope?: { status: string; calls: { id: number; purpose: string; reason: string; source?: string; quotes?: string[] }[]; review_reasons: string[] }; history: History; actions: { id: number; action: string; actor: string; created_at: string; payload: { note?: string; resolution?: string } }[] };
const analysisLabels: Record<string, string> = { queued: "排队中", processing: "分析中", waiting_evidence: "等待录音/转写", needs_review: "录音待核对", succeeded: "复盘完成", failed: "分析失败", not_requested: "未发起" };
const caseLabels: Record<string, string> = { open: "待处理", in_progress: "处理中", resolved: "已解决", closed: "已结案" };
function stamp(value: string | null) { return value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "—"; }
function localTime(value: string | null) {
  if (!value) return "";
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(value));
  const p = (key: string) => parts.find(item => item.type === key)?.value;
  return `${p("year")}-${p("month")}-${p("day")}T${p("hour")}:${p("minute")}`;
}
function iso(value: string) { return value ? new Date(`${value}:00+08:00`).toISOString() : null; }

export function DeliveryHistory({ orderId }: { orderId: number }) {
  const query = useQuery({ queryKey: ["delivery-history", orderId], queryFn: () => workflowRequest<History>(`/orders/${orderId}/history`), retry: false });
  return <details className="delivery-help"><summary>转派历史</summary>{query.error && <p role="alert">{query.error.message}</p>}{query.data && <HistoryList history={query.data} />}</details>;
}

function HistoryList({ history }: { history: History }) {
  return <><ol className="delivery-timeline">{history.assignments.map(row => <li key={row.id}><strong>{row.staff_name || "未分配"}</strong><span>{stamp(row.valid_from)} 至 {row.valid_to ? stamp(row.valid_to) : "现在"}{row.baseline ? " · 上线观察基线" : ""}</span></li>)}</ol>{!history.assignments.length && <p>暂无记录</p>}<small>基线之前的归属不作历史确认。</small></>;
}

export function DeliveryWorkflow({ initialCaseId }: { initialCaseId: number | null }) {
  const [selected, setSelected] = useState(initialCaseId);
  const [cursor, setCursor] = useState<number | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { if (initialCaseId !== null) setSelected(initialCaseId); }, [initialCaseId]);
  const config = useQuery({ queryKey: ["delivery-workflow-status"], queryFn: () => workflowRequest<{ config: { auto_analysis: boolean }; asr_configured: boolean; llm_configured: boolean; reasoning_model?: string }>("/workflow/status"), retry: false });
  const cases = useQuery({ queryKey: ["delivery-cases", cursor], queryFn: () => workflowRequest<{ items: CaseRow[]; has_more: boolean; next_before_id: number | null }>(`/cases${cursor ? `?before_id=${cursor}` : ""}`), refetchInterval: 15000, retry: false });
  const [saving, setSaving] = useState(false);
  async function toggleAuto(enabled: boolean) {
    setError(""); setSaving(true);
    try { await workflowRequest("/workflow/settings", { auto_analysis: enabled }); await config.refetch(); }
    catch (error) { setError(error instanceof Error ? error.message : "保存失败"); }
    finally { setSaving(false); }
  }
  return <div className="delivery-workflow"><Card title="异常处理" action={<button type="button" onClick={() => void cases.refetch()}>刷新</button>}>
    {config.data && <label className="delivery-push-toggle"><input type="checkbox" checked={config.data.config.auto_analysis} disabled={saving} onChange={event => void toggleAuto(event.target.checked)} />新发生的配送失败自动转写与 DeepSeek 判断</label>}
    {config.data && (!config.data.asr_configured || !config.data.llm_configured) && <p role="alert">转写或模型配置缺失</p>}
    {config.data?.reasoning_model && <p>判断模型：{config.data.reasoning_model}（复用系统 AI 配置）；语音识别沿用现有转写服务。</p>}
    {[error, config.error?.message, cases.error?.message].filter(Boolean).map((message, index) => <p role="alert" key={index}>{message}</p>)}
    {cases.isPending && <p role="status">读取异常单…</p>}
    {cases.data?.items.length === 0 && <p>暂无异常单。历史订单可从“无法配送 / 未成交”手动发起复盘。</p>}
    <div className="delivery-table"><table><thead><tr><th>意向单</th><th>原因</th><th>分析</th><th>处理</th><th>负责人 / 截止</th><th /></tr></thead><tbody>{cases.data?.items.map(row => <tr key={row.id}><td>#{row.order_id}<br />{row.customer_name}</td><td>{row.reported_reason || "未填写"}</td><td>{analysisLabels[row.analysis_status] || row.analysis_status}</td><td>{caseLabels[row.status]}</td><td>{row.owner_name || "待分配"}<br />{stamp(row.due_at)}</td><td><button type="button" onClick={() => setSelected(row.id)}>处理 / 复盘</button></td></tr>)}</tbody></table></div>
    <div className="delivery-controls"><button type="button" disabled={!cursor} onClick={() => setCursor(null)}>回到最新</button><button type="button" disabled={!cases.data?.has_more} onClick={() => setCursor(cases.data!.next_before_id)}>下一页</button></div>
  </Card>{selected !== null && <CaseEditor key={selected} caseId={selected} onClose={() => setSelected(null)} />}</div>;
}

function CaseEditor({ caseId, onClose }: { caseId: number; onClose: () => void }) {
  const section = useRef<HTMLDivElement>(null);
  const query = useQuery({ queryKey: ["delivery-case", caseId], queryFn: () => workflowRequest<CaseDetail>(`/cases/${caseId}`), refetchInterval: 15000, retry: false });
  const owners = useQuery({ queryKey: ["delivery-case-owners"], queryFn: () => workflowRequest<{ items: { id: number; real_name: string }[] }>("/cases/owners"), retry: false });
  const [form, setForm] = useState({ version: 0, status: "open", review_status: "pending", owner: "", due: "", callback: "", resolution: "", note: "" });
  const [dirty, setDirty] = useState(false);
  const [selection, setSelection] = useState<Record<number, string>>({});
  const [selectionDirty, setSelectionDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    const data = query.data; if (!data) return;
    if (!dirty) setForm({ version: data.version, status: data.status, review_status: data.review_status, owner: data.owner_user_id ? String(data.owner_user_id) : "", due: localTime(data.due_at), callback: localTime(data.callback_at), resolution: data.resolution ?? "", note: "" });
    if (!selectionDirty) setSelection(Object.fromEntries((data.selected_calls ?? []).filter(row => row.purpose).map(row => [row.id, row.role])));
  }, [query.data, dirty, selectionDirty]);
  function edit(key: Exclude<keyof typeof form, 'version'>, value: string) { setForm(previous => ({ ...previous, [key]: value })); setDirty(true); }
  async function save() {
    setBusy(true); setError(""); setMessage("");
    try {
      await workflowRequest(`/cases/${caseId}/update`, { version: form.version, status: form.status, review_status: form.review_status, owner_user_id: form.owner ? Number(form.owner) : null, due_at: iso(form.due), callback_at: iso(form.callback), resolution: form.resolution, note: form.note });
      await query.refetch(); setDirty(false); setMessage("处理记录已保存");
    } catch (error) { setError(error instanceof Error ? error.message : "保存失败"); }
    finally { setBusy(false); }
  }
  async function analyze() {
    if (!query.data) return;
    const selected_calls = Object.entries(selection).filter(([, role]) => !!role).map(([id, role]) => ({ id: Number(id), role, purpose: role === 'outbound' ? 'outbound_sale' : 'delivery_failure' }));
    if (!selected_calls.length) { setError("请选择外呼成单沟通或直接涉及本次无法配送的沟通，不要全选普通联系记录"); return; }
    if (selected_calls.length > 2 || new Set(selected_calls.map(call => call.role)).size !== selected_calls.length) { setError('外呼成单和本次无法配送沟通各选一通，不确定的一侧留待确认'); return; }
    const retry_failed = query.data.analysis_status === "failed";
    if (!window.confirm(retry_failed ? "确认所选录音直接涉及成单或本次失败，并重新提交失败转写？这可能产生新的费用。" : "确认所选录音是外呼成单沟通或直接涉及本次无法配送的沟通？仅处理所选录音；就绪后按当前反馈通知设置续发图片，普通联系记录不要选择。")) return;
    setBusy(true); setError("");
    try { await workflowRequest(`/cases/${caseId}/analyze`, { version: query.data.version, selected_calls, retry_failed }); await query.refetch(); setSelectionDirty(false); setMessage("分析任务已提交"); }
    catch (error) { setError(error instanceof Error ? error.message : "提交失败"); }
    finally { setBusy(false); }
  }
  const data = query.data;
  const loaded = !!data;
  useEffect(() => { if (loaded) section.current?.scrollIntoView?.({ block: "start" }); }, [caseId, loaded]);
  return <div ref={section}><Card title={`异常单 #${caseId}`} action={<button type="button" onClick={onClose}>关闭详情</button>}>
    {query.error && <p role="alert">{query.error.message}</p>}{query.isPending && <p role="status">读取中…</p>}
    {data && <>
      <div className="delivery-controls"><strong>意向单 #{data.order_id}</strong><span className="delivery-status-badge">{analysisLabels[data.analysis_status]}</span><span>{data.review_status === "confirmed" ? "已人工确认" : "待人工复核"}</span></div>
      {data.analysis_error && <p>{data.analysis_error}</p>}
      <DeliveryDialogues caseId={caseId} calls={data.candidates.items} />
      {data.material_scope && <section className="delivery-help"><h3>本次反馈的录音筛选</h3><p>DeepSeek 先选择少量候选；缺文字时自动转写，再按业务内容判断，每侧最多一通。不需要人工选定；没有证据时不会编造录音。</p>{data.material_scope.calls.map(call => <div key={call.id}><p>录音 {call.id} · {call.purpose === 'outbound_sale' ? '外呼成单沟通' : '本次无法配送沟通'} · {call.source === 'manual_business_confirmation' ? '人工选定' : 'AI 辅助筛选，不代表反馈原因已核实'}</p>{call.quotes?.map((quote, index) => <blockquote key={index}>{quote}</blockquote>)}</div>)}{!data.material_scope.calls.length && <p>{data.material_scope.status === 'no_relevant' ? 'DeepSeek 已完成判断，现有录音未发现本次成单或失败沟通。' : data.material_scope.status === 'historical' ? '启用前的历史资料保留，不自动补转。' : data.material_scope.status === 'ai_unavailable' ? '录音筛选失败，已停止自动重试，请查看具体原因。' : data.material_scope.status === 'retry_pending' ? '录音筛选失败，等待有限重试。' : data.material_scope.status === 'awaiting_transcripts' ? '已选定录音，正在转写；完成后单独核验内容。' : '等待筛选或补齐本单录音，不需要人工选定。'}</p>}{data.material_scope.review_reasons.map((reason, index) => <p key={index}>处理说明：{reason}</p>)}</section>}
      {data.analysis && <section className="delivery-analysis-result"><h3>双侧录音复盘</h3>{data.analysis.validation_status === "partial" && <p className="delivery-muted">以下为待核实线索，并非已确认的失败原因；已剔除 {data.analysis.omitted_findings ?? 0} 条引用不合格判断。</p>}<p>{data.analysis.summary}</p>{data.analysis.findings.map((finding, index) => <details key={index}><summary>{({ fact: "AI 判断，待核实", inference: "AI 判断，待核实", uncertain: "待核实" } as Record<string, string>)[finding.kind]}：{finding.issue}</summary>{finding.refs.map(ref => { const source = data.analysis!.references[ref]; return source && <blockquote key={ref}><small>{source.role === "delivery" ? "配送" : "外呼"} · {source.begin_ms == null ? "时间点未知" : `${(source.begin_ms / 1000).toFixed(1)} 秒`} · {ref}</small><p>{source.text}</p></blockquote>; })}</details>)}<h4>处理建议</h4><ul>{data.analysis.next_actions.map((value, index) => <li key={index}>{value}</li>)}</ul>{data.analysis.evidence_gaps.length > 0 && <p>待补证据：{data.analysis.evidence_gaps.join("；")}</p>}</section>}
      <details className="delivery-help" open={data.analysis_status !== "succeeded"}><summary>录音与转写</summary><p>以下选择仅用于可选纠正，不操作也会由系统自动处理。</p>
        {data.candidates.has_more && <p>候选超过 200 条，请优先核对关键录音。</p>}
        <div className="delivery-table"><table><thead><tr><th>可选人工纠正（非必填）</th><th>人员 / 时间</th><th>转写</th></tr></thead><tbody>{data.candidates.items.map(call => <tr key={call.id}><td><select aria-label={`录音 ${call.id} 归属`} value={selection[call.id] ?? ""} disabled={busy} onChange={event => { setSelection(previous => ({ ...previous, [call.id]: event.target.value })); setSelectionDirty(true); }}><option value="">不选</option><option value="outbound">外呼成单沟通</option><option value="delivery">本次无法配送沟通</option></select><small>{call.role === "unverified" ? "身份待核对" : ""}</small></td><td>{call.user_name}<br />{stamp(call.call_time)}</td><td><details><summary>{call.delivery_transcript?.full_text || call.full_text ? "查看文字" : call.delivery_transcript?.status === "failed" ? "转写失败" : call.recording_available ? "录音已到达" : "暂无录音"}</summary><p className="delivery-transcript">{call.delivery_transcript?.full_text || call.full_text || call.delivery_transcript?.error || "暂无文字"}</p></details></td></tr>)}</tbody></table></div>
        <button type="button" disabled={busy || data.status === "closed" || data.analysis_status === "processing"} onClick={() => void analyze()}>确认相关录音并处理</button>
      </details>
      <fieldset disabled={busy} className="delivery-case-form"><legend>处理记录（北京时间）</legend>
        <label>负责人<select value={form.owner} onChange={event => edit("owner", event.target.value)}><option value="">待分配</option>{owners.data?.items.map(owner => <option value={owner.id} key={owner.id}>{owner.real_name}</option>)}</select></label>
        <label>处理期限<input type="datetime-local" value={form.due} onChange={event => edit("due", event.target.value)} /></label>
        <label>预约回拨<input type="datetime-local" value={form.callback} onChange={event => edit("callback", event.target.value)} /></label>
        <label>处理状态<select value={form.status} onChange={event => edit("status", event.target.value)}>{Object.entries(caseLabels).map(([key, value]) => <option value={key} key={key}>{value}</option>)}</select></label>
        <label>人工复核<select value={form.review_status} onChange={event => edit("review_status", event.target.value)}><option value="pending">待复核</option><option value="confirmed">已确认</option><option value="needs_more">需补充证据</option></select></label>
        <label className="delivery-form-wide">处理结果<textarea value={form.resolution} maxLength={4000} onChange={event => edit("resolution", event.target.value)} /></label>
        <label className="delivery-form-wide">本次跟进备注<textarea value={form.note} maxLength={2000} onChange={event => edit("note", event.target.value)} /></label>
      </fieldset>
      {owners.error && <p role="alert">{owners.error.message}</p>}
      <div className="delivery-controls"><button className="delivery-primary" type="button" disabled={busy} onClick={() => void save()}>保存处理记录</button><button type="button" disabled={busy} onClick={() => { setDirty(false); void query.refetch(); }}>重新载入</button></div>
      <details className="delivery-help"><summary>分配 / 转派历史</summary><HistoryList history={data.history} /></details>
      <details className="delivery-help"><summary>操作记录</summary>{data.actions.map(action => <p key={action.id}>{stamp(action.created_at)} · {action.actor === "delivery_ai" ? "AI 分析" : "对接端操作"} · {action.action}{action.payload.note ? ` · ${action.payload.note}` : ""}{action.payload.resolution ? ` · ${action.payload.resolution}` : ""}</p>)}</details>
    </>}{error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
  </Card></div>;
}

export function DeliveryAlerts() {
  const query = useQuery({ queryKey: ["delivery-alerts"], queryFn: async () => {
    const config = await workflowRequest<{ config: { first_call_minutes: number } }>("/notifications");
    return workflowRequest<{ items: { event_key: string; kind: string; order_id: number; staff_name: string | null; due_at: string }[]; truncated: boolean }>(`/alerts?first_call_minutes=${config.config.first_call_minutes}`);
  }, refetchInterval: 30000, retry: false });
  const labels: Record<string, string> = { first_call: "暂无首拨记录", callback: "预约回拨到期", case_due: "异常处理逾期", appointment: "预约配送待确认" };
  return <Card title="超时待办" action={<button type="button" onClick={() => void query.refetch()}>刷新</button>}>
    {query.error && <p role="alert">{query.error.message}</p>}{query.isPending && <p role="status">读取中…</p>}{query.data?.items.length === 0 && <p>暂无超时待办</p>}
    {query.data?.truncated && <p>待办较多，当前为前 500 条／类型。</p>}
    <div className="delivery-table"><table><thead><tr><th>意向单</th><th>待办</th><th>负责人</th><th>到期时间</th></tr></thead><tbody>{query.data?.items.map(row => <tr key={row.event_key}><td>#{row.order_id}</td><td>{labels[row.kind]}</td><td>{row.staff_name || "待分配"}</td><td>{stamp(row.due_at)}</td></tr>)}</tbody></table></div>
  </Card>;
}
