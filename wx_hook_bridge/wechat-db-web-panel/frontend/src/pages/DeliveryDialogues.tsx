import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { API_BASE } from '../lib/api';
import { workflowRequest } from './DeliveryWorkflow';

type ImageRow = { id: string; call_id: number; title: string; status: string; error: string | null; width: number; height: number; receipts: { room_name: string; status: string; error: string | null }[]; context_receipts?: { room_id: string; status: string; error: string | null }[]; quote_receipts?: { room_id: string; status: string; error: string | null }[] };
type PlanRow = { image_id: string; room_id: string; room_name: string; title: string; body: string; status: string; context_status: string; feedback_body?: string; feedback_status?: string };
type AutoState = { case_id: number; status: string; error: string | null };
const autoLabels: Record<string, string> = { waiting_reference: '等待原通知的真实消息编号，不发送普通图片', awaiting_quote_evidence: '等待图片引用入库确认，不重发', no_relevant: 'DeepSeek 已完成核对，未发现相关录音，不发送无关图片', waiting_source: '等待本单录音资料到达', ai_unavailable: 'DeepSeek 判断暂不可用', waiting_ai: 'DeepSeek 自动处理或等待录音资料，无需人工选定', needs_review: '等待本单录音证据', submitted: '资料已提交桥接，群内显示待核对', waiting_materials: '等待已确认录音或新资料就绪，已提交材料不重发', excluded: '不在当前自动续发范围', blocked: '提交结果待核对，自动续发已停止', image_failed: '图片生成失败，需人工重试', error: '自动资料处理暂不可用' };
const labels: Record<string, string> = { ready: '图片生成成功', generating: '生成中', failed: '生成失败', bridge_accepted: '桥接已接受，群内可见待核对', uncertain: '结果不明，禁止重复发送', sending: '正在提交桥接', local_verified: '已查库确认图片引用原通知，客户端显示待核对', awaiting_evidence: '桥接已接受，等待图片引用入库证据', rejected: '引用图片被拒绝，不降级、不自动重试' };
export function DeliveryDialogues({ caseId, calls }: { caseId: number; calls: { id: number; call_time: string; delivery_transcript?: { status: string } | null }[] }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<string | null>(null);
  const [rooms, setRooms] = useState<string[]>([]);
  const [chosen, setChosen] = useState<Record<number, string>>({});
  const [plan, setPlan] = useState<PlanRow[] | null>(null);
  const [planKey, setPlanKey] = useState('');
  const [archiveUrl, setArchiveUrl] = useState('');
  const images = useQuery({ queryKey: ['delivery-dialogues', caseId], queryFn: () => workflowRequest<{ items: ImageRow[] }>(`/cases/${caseId}/dialogues`), refetchInterval: 3000, retry: false });
  const config = useQuery({ queryKey: ['delivery-dialogue-groups'], queryFn: () => workflowRequest<{ config: { target_groups: { room_id: string; name: string }[]; failure_notifications_enabled?: boolean }; materials?: { running: boolean; items: AutoState[] } }>('/notifications'), retry: false, refetchInterval: 10000 });
  const automatic = config.data?.materials?.items.find(item => item.case_id === caseId);
  const url = (id: string) => `${API_BASE}/delivery-management/dialogues/${id}/image`;
  const selectionKey = JSON.stringify({ chosen, rooms });
  const validPlan = plan !== null && planKey === selectionKey;
  const canPreview = !!rooms.length && !!Object.keys(chosen).length;
  async function loadArchive() {
    setError('');
    try { const link = await workflowRequest<{ url: string }>(`/cases/${caseId}/archive-link`, {}); setArchiveUrl(link.url); }
    catch (e) { setError(e instanceof Error ? e.message : '档案链接读取失败'); }
  }
  async function generate(callId: number, regenerate: boolean) {
    setBusy(true); setError(''); setMessage('正在生成完整长图…');
    try { await workflowRequest(`/cases/${caseId}/dialogues/generate`, { call_id: callId, regenerate }); await images.refetch(); setMessage('生成任务已处理，请查看每通录音状态'); }
    catch (e) { setError(e instanceof Error ? e.message : '生成失败'); }
    finally { setBusy(false); }
  }
  async function send(simulate: boolean) {
    if (!simulate && (!validPlan || !window.confirm('按预览发送：本次无法配送一条反馈文字（含组别、完整电话和两侧独立录音链接） → 对应长图。历史已提交资料跳过，结果不明则停止。确认真实发送至所选配送群？'))) return;
    setBusy(true); setError(''); setMessage('');
    try { const result = await workflowRequest<{ items: PlanRow[] }>(`/cases/${caseId}/dialogues/send`, { image_ids: Object.values(chosen), room_ids: rooms, simulate }); if (simulate) { setPlan(result.items); setPlanKey(selectionKey); } else { setPlan(null); } await images.refetch(); setMessage(simulate ? '模拟检查通过，未调用微信发送接口' : '发送流程结束，请查看各群回执；不等同于群内可见'); }
    catch (e) { setError(e instanceof Error ? e.message : '发送失败'); }
    finally { setBusy(false); }
  }
  return <section className="delivery-dialogues"><h3>录音对话长图</h3><p>每通录音一张完整图 · A/B 为分轨标签</p>
    <p>历史图片和回执保留，不代表全部关联录音已入选本次反馈资料；业务用途见下方录音筛选。</p>
    {config.data?.config.failure_notifications_enabled && <p>新反馈主消息提交后，仅续发已筛选的外呼成单及本次失败沟通；普通联系记录不自动转写或发图。</p>}
    {automatic && <p role="status">自动资料：{autoLabels[automatic.status] || automatic.status}{automatic.error ? ` · ${automatic.error}` : ''}</p>}
    <div className="delivery-controls"><button disabled={busy} onClick={() => void loadArchive()}>获取云盛订单档案链接</button>{archiveUrl && <a href={archiveUrl} target="_blank" rel="noreferrer">打开订单通话档案（需登录）</a>}</div>
    {[...calls].sort((a, b) => a.call_time.localeCompare(b.call_time)).map(call => <div className="delivery-dialogue-call" key={call.id}>
      <strong>录音 {call.id}</strong><button disabled={busy || call.delivery_transcript?.status !== 'succeeded'} onClick={() => void generate(call.id, false)}>生成长图</button>
      {images.data?.items.filter(row => row.call_id === call.id).map(row => <div key={row.id}><label><input type="checkbox" disabled={busy || row.status !== 'ready'} checked={chosen[call.id] === row.id} onChange={e => setChosen(old => { const next = { ...old }; if (e.target.checked) next[call.id] = row.id; else delete next[call.id]; return next; })} />{row.title} · {labels[row.status] || row.status}{row.height ? ` · ${row.width} × ${row.height}` : ''}</label>
        {row.error && <p role="alert">{row.error}</p>}{row.status === 'ready' && <div className="delivery-controls"><button onClick={() => setPreview(preview === row.id ? null : row.id)}>预览长图</button><a href={`${url(row.id)}?download=true`}>下载长图</a><button disabled={busy} onClick={() => void generate(call.id, true)}>重新生成</button></div>}
        {preview === row.id && row.status === 'ready' && <a href={url(row.id)} target="_blank" rel="noreferrer"><img className="delivery-dialogue-preview" src={url(row.id)} alt={`${row.title} ${row.call_id} 完整转写长图`} /></a>}
        {row.receipts.map((r, i) => <p key={i}>{r.room_name}：{labels[r.status] || r.status}{r.error ? ` · ${r.error}` : ''}</p>)}
        {row.quote_receipts?.map(r => <p key={`quote:${r.room_id}`}>原生图片引用：{labels[r.status] || r.status}{r.error ? ` · ${r.error}` : ''}</p>)}
        {row.context_receipts?.map(r => <p key={r.room_id}>{config.data?.config.target_groups.find(g => g.room_id === r.room_id)?.name || r.room_id} · 历史说明消息：{labels[r.status] || r.status}{r.error ? ` · ${r.error}` : ''}</p>)}
      </div>)}
    </div>)}
    <fieldset disabled={busy}><legend>手动图片发送（不改变文字通知开关）</legend>{config.data?.config.target_groups.map(g => <label key={g.room_id}><input type="checkbox" checked={rooms.includes(g.room_id)} onChange={e => setRooms(old => e.target.checked ? [...old, g.room_id] : old.filter(r => r !== g.room_id))} />{g.name}</label>)}
      {!config.data?.config.target_groups.length && <p>请先在配送推送设置中选择配送群；不会自动补发历史图片。</p>}
      <p>本次无法配送反馈就是主消息，无需搜索群历史。新资料发送顺序：一条完整反馈文字 → 对应长图；每张图前不再追加说明，主消息已提交则不重复发送。</p>
      <div className="delivery-controls"><button disabled={!canPreview} onClick={() => void send(true)}>预览群消息 / 模拟检查</button><button disabled={!validPlan || !canPreview} onClick={() => void send(false)}>发送所选长图到所选群</button></div>
      {validPlan && <div className="delivery-message-plan"><h4>群消息预览与发送状态</h4>{plan.map((p, index) => <article key={`${p.room_id}-${p.image_id}`}><strong>{p.room_name} · {p.title}</strong>{!plan.slice(0, index).some(previous => previous.room_id === p.room_id) && p.feedback_body && <><h5>本次反馈主消息</h5><pre>{p.feedback_body}</pre><small>{p.feedback_status === 'sent' ? '桥接已接受，复用主消息记录；不证明群内可见' : `主消息状态：${p.feedback_status || 'pending'}`}</small></>}{p.status === 'bridge_accepted' ? <p>此版本图片已提交，本次跳过，不自动补发主消息、说明或图片。</p> : p.status !== 'pending' || ['uncertain', 'sending'].includes(p.context_status) || !['pending', 'sent', undefined].includes(p.feedback_status) ? <p role="alert">已有排队或结果不明的提交，停止该群后续发送，请先核对。</p> : <p>取得原通知真实消息编号后发送原生引用的{p.title}长图；查不到则暂缓，不发普通图片</p>}</article>)}</div>}
    </fieldset>{(error || images.error || config.error) && <p role="alert">{error || images.error?.message || config.error?.message}</p>}{message && <p role="status">{message}</p>}
  </section>;
}
