import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { API_BASE } from "../lib/api";
import { Card } from "../components/ui";
import { DeliveryPushSettings } from "./DeliveryPushSettings";
import { DeliveryAlerts, DeliveryWorkflow, DeliveryHistory, workflowRequest } from "./DeliveryWorkflow";
import "./delivery-management.css";

type Staff = { staff_id: number; staff_name: string; first_call_at: string | null; first_order_call_at: string | null; call_count: number; dialed_customer_count: number; connected_customer_count: number; order_customer_count: number; connected_order_customer_count: number; unmatched_call_count: number; ambiguous_call_count: number };
type Call = { id: number; staff_name: string; call_time: string; call_duration: number; is_connected: boolean; customer_phone_masked: string; order_id: number | null; match_status: string; recording_available: boolean };
type Failure = { order_id: number; customer_name: string; customer_phone_masked: string; assigned_delivery_name: string | null; delivery_failure_reason: string | null; delivery_remark: string | null };
type Evidence = { id: number; user_name: string; role: string; call_time: string; recording_available: boolean; transcript_status: string | null; full_text: string | null; ai_summary: string | null };
type Page<T> = { items: T[]; has_more: boolean; next_before_id: number | null };

async function get<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}/delivery-management${path}`, { signal });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `读取失败 (${response.status})`);
  }
  return response.json();
}

function localDay() {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
  return ["year", "month", "day"].map(key => parts.find(part => part.type === key)?.value).join("-");
}

function stamp(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "暂无记录";
}

export function DeliveryManagementPage() {
  const [day, setDay] = useState(localDay);
  const [tab, setTab] = useState<"calls" | "failures" | "cases" | "alerts">("calls");
  const [caseId, setCaseId] = useState<number | null>(null);
  const [actionError, setActionError] = useState("");
  const [creating, setCreating] = useState(false);
  const [callCursor, setCallCursor] = useState<number | null>(null);
  const [failureCursor, setFailureCursor] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [evidenceCursor, setEvidenceCursor] = useState<number | null>(null);
  const evidenceSection = useRef<HTMLDivElement>(null);
  useEffect(() => { if (selected !== null) evidenceSection.current?.scrollIntoView({ block: "start" }); }, [selected]);
  const overview = useQuery({ queryKey: ["delivery-management", "overview", day], queryFn: ({ signal }) => get<{ items: Staff[]; snapshot_at: string }>(`/overview?day=${day}`, signal), retry: false });
  const calls = useQuery({ queryKey: ["delivery-management", "calls", day, callCursor], queryFn: ({ signal }) => get<Page<Call>>(`/calls?day=${day}&limit=50${callCursor ? `&before_id=${callCursor}` : ""}`, signal), enabled: tab === "calls", retry: false });
  const failures = useQuery({ queryKey: ["delivery-management", "failures", failureCursor], queryFn: ({ signal }) => get<Page<Failure>>(`/failures?limit=50${failureCursor ? `&before_id=${failureCursor}` : ""}`, signal), enabled: tab === "failures", retry: false });
  const evidence = useQuery({ queryKey: ["delivery-management", "evidence", selected, evidenceCursor], queryFn: ({ signal }) => get<Page<Evidence>>(`/orders/${selected}/evidence?limit=50${evidenceCursor ? `&before_id=${evidenceCursor}` : ""}`, signal), enabled: selected !== null, retry: false });
  const totals = (overview.data?.items ?? []).reduce((sum, row) => ({ calls: sum.calls + row.call_count, connected: sum.connected + row.connected_customer_count, unmatched: sum.unmatched + row.unmatched_call_count + row.ambiguous_call_count }), { calls: 0, connected: 0, unmatched: 0 });
  function selectOrder(id: number) { setSelected(id); setEvidenceCursor(null); }
  async function createCase(orderId: number) {
    if (!window.confirm("为该历史订单创建异常复盘？缺失的录音文字将提交转写。")) return;
    setCreating(true); setActionError("");
    try { const result = await workflowRequest<{ id: number }>(`/orders/${orderId}/analysis`, {}); setCaseId(result.id); setTab("cases"); setSelected(null); }
    catch (error) { setActionError(error instanceof Error ? error.message : "创建失败"); }
    finally { setCreating(false); }
  }
  const active = tab === "calls" ? calls : failures;
  return <div className="delivery-management">
    <header className="delivery-heading"><div><h1>配送管理</h1><p>真实通话 · 跟进统计 · 异常证据</p></div><div className="delivery-controls"><label>统计日期（北京时间）<input type="date" value={day} onChange={event => { if (event.target.value) { setDay(event.target.value); setCallCursor(null); setSelected(null); } }} /></label><button type="button" disabled={overview.isFetching || active.isFetching} onClick={() => { void overview.refetch(); void active.refetch(); }}>刷新数据</button></div></header>
    <DeliveryPushSettings day={day} overview={overview.data} call={calls.data?.items.find(row => row.match_status === "matched")} />
    {overview.error && <p role="alert">{overview.error.message}</p>}
    {overview.isPending ? <p role="status">正在读取配送统计…</p> : overview.data && <>
      <div className="delivery-metrics"><Card title="配送员拨号次数"><strong>{totals.calls}</strong></Card><Card title="接通客户（各人合计）"><strong>{totals.connected}</strong><p>各人号码去重，跨人可能重复</p></Card><Card title="待核对通话"><strong>{totals.unmatched}</strong><p>未关联或同时匹配多单</p></Card></div>
      <Card title="每日跟进统计" action={<span className="delivery-muted">更新于 {stamp(overview.data.snapshot_at)}</span>}><details className="delivery-help"><summary>统计口径</summary><p>首拨含全部 EC 电话；意向单仅计唯一匹配，按当前分配关系计算。各人号码去重，跨人可能重复。零记录不代表同步正常。</p></details><div className="delivery-table"><table><thead><tr><th>配送员</th><th>首拨</th><th>意向单首拨</th><th>拨号次数</th><th>拨打 / 接通客户</th><th>意向单客户 / 接通</th><th>待核对通话</th></tr></thead><tbody>{overview.data.items.map(row => <tr key={row.staff_id}><td>{row.staff_name}</td><td>{stamp(row.first_call_at)}</td><td>{stamp(row.first_order_call_at)}</td><td>{row.call_count}</td><td>{row.dialed_customer_count} / {row.connected_customer_count}</td><td>{row.order_customer_count} / {row.connected_order_customer_count}</td><td>{row.unmatched_call_count + row.ambiguous_call_count}</td></tr>)}</tbody></table></div></Card>
    </>}
    <div className="delivery-controls" aria-label="配送管理视图"><button type="button" aria-pressed={tab === "calls"} onClick={() => setTab("calls")}>通话明细</button><button type="button" aria-pressed={tab === "failures"} onClick={() => setTab("failures")}>无法配送 / 未成交</button><button type="button" aria-pressed={tab === "cases"} onClick={() => setTab("cases")}>异常处理</button><button type="button" aria-pressed={tab === "alerts"} onClick={() => setTab("alerts")}>超时待办</button></div>
    {actionError && <p role="alert">{actionError}</p>}
    {tab === "cases" ? <DeliveryWorkflow initialCaseId={caseId} /> : tab === "alerts" ? <DeliveryAlerts /> : <Card title={tab === "calls" ? `${day} 通话明细` : "当前异常订单（不限统计日期）"}>
      {active.isPending && <p role="status">正在读取…</p>}{active.error && <p role="alert">{active.error.message}</p>}
      {active.data?.items.length === 0 && <p>没有符合条件的记录。</p>}
      {tab === "calls" && calls.data && <><div className="delivery-table"><table><thead><tr><th>配送员 / 时间</th><th>客户号码</th><th>接通情况</th><th>意向单</th><th>录音</th></tr></thead><tbody>{calls.data.items.map(row => <tr key={row.id}><td>{row.staff_name}<br />{stamp(row.call_time)}</td><td>{row.customer_phone_masked}</td><td>{row.is_connected ? `接通 ${row.call_duration} 秒` : "未接通"}</td><td>{row.order_id ? <button type="button" onClick={() => selectOrder(row.order_id!)}>查看 #{row.order_id}</button> : row.match_status === "ambiguous" ? "多单匹配，待核对" : "未关联意向单"}</td><td>{row.recording_available ? "有录音" : "暂无录音"}</td></tr>)}</tbody></table></div><div className="delivery-controls"><button type="button" disabled={!callCursor} onClick={() => setCallCursor(null)}>回到最新</button><button type="button" disabled={!calls.data.has_more || calls.isFetching} onClick={() => setCallCursor(calls.data!.next_before_id)}>下一页</button></div></>}
      {tab === "failures" && failures.data && <><div className="delivery-table"><table><thead><tr><th>意向单</th><th>客户</th><th>当前配送员</th><th>原始反馈</th><th>证据</th></tr></thead><tbody>{failures.data.items.map(row => <tr key={row.order_id}><td>#{row.order_id}</td><td>{row.customer_name} {row.customer_phone_masked}</td><td>{row.assigned_delivery_name || "未分配"}</td><td>{row.delivery_failure_reason || row.delivery_remark || "未填写原因"}</td><td><div className="delivery-controls"><button type="button" onClick={() => selectOrder(row.order_id)}>查看录音文字</button><button type="button" disabled={creating} onClick={() => void createCase(row.order_id)}>录音复盘</button></div></td></tr>)}</tbody></table></div><div className="delivery-controls"><button type="button" disabled={!failureCursor} onClick={() => setFailureCursor(null)}>回到最新</button><button type="button" disabled={!failures.data.has_more || failures.isFetching} onClick={() => setFailureCursor(failures.data!.next_before_id)}>下一页</button></div></>}
    </Card>}
    {selected !== null && <div ref={evidenceSection}><Card title={`意向单 #${selected} · 录音证据`} action={<button type="button" onClick={() => setSelected(null)}>关闭证据</button>}>
      <p className="delivery-muted">候选通话，归属待核对</p>
      <DeliveryHistory orderId={selected} />
      {evidence.isPending && <p role="status">读取证据中…</p>}{evidence.error && <p role="alert">{evidence.error.message}</p>}{evidence.data?.items.length === 0 && <p>没有候选通话，证据不足。</p>}
      {evidence.data?.items.map(row => <article key={row.id} className="delivery-evidence"><h3>{row.user_name} · {({ delivery: "当前配送员", outbound: "外呼员", ambiguous: "身份冲突", unverified: "身份待核对" } as Record<string, string>)[row.role] || "待核对"}</h3><p>{stamp(row.call_time)} · {row.recording_available ? "有录音" : "暂无录音"}</p><p className="delivery-transcript">{row.full_text || (row.transcript_status === "failed" ? "转写失败，暂无可用文字" : "尚无转写文字")}</p>{row.ai_summary && <p>已有单通摘要（非联合归因）：{row.ai_summary}</p>}</article>)}
      {evidence.data && <div className="delivery-controls"><button type="button" disabled={!evidenceCursor} onClick={() => setEvidenceCursor(null)}>回到最新</button><button type="button" disabled={!evidence.data.has_more || evidence.isFetching} onClick={() => setEvidenceCursor(evidence.data!.next_before_id)}>下一页证据</button></div>}
    </Card></div>}
  </div>;
}
