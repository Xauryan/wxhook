import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { API_BASE } from '../lib/api';

type Room = { room_id: string; name: string };
type Person = { wxid: string; name: string };
type Settings = { revision: number; account: string | null; teams: { id: string; name: string }[]; kinds: string[];
  selections: { room_id: string; team_id: string; members: Person[] }[] };
type Members = { room_id: string; account: string; items: Person[] };
type Draft = Record<string, Record<string, string[]>>;

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}/delivery-management/notifications/${path}`, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '@ 设置请求失败');
  return data;
}

function initialDraft(settings: Settings): Draft {
  const result: Draft = {};
  for (const row of settings.selections ?? []) {
    result[row.room_id] ??= {};
    result[row.room_id][row.team_id] = row.members.map(m => m.wxid);
  }
  return result;
}

export function DeliveryMentionSettings({ rooms, disabled = false }: { rooms: Room[]; disabled?: boolean }) {
  const config = useQuery({ queryKey: ['delivery-mentions'], queryFn: () => request<Settings>('mentions'), retry: false });
  const [room, setRoom] = useState('');
  const [saved, setSaved] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  useEffect(() => {
    if (config.data?.teams && !dirty) { setSaved(config.data); setDraft(initialDraft(config.data)); }
  }, [config.data, dirty]);
  useEffect(() => {
    if (!rooms.some(r => r.room_id === room)) setRoom(rooms[0]?.room_id ?? '');
  }, [rooms, room]);
  const members = useQuery({ queryKey: ['delivery-mention-members', room],
    queryFn: () => request<Members>(`mention-members?room_id=${encodeURIComponent(room)}`), enabled: !!room, retry: false });
  const known = new Map((saved?.selections ?? []).filter(r => r.room_id === room).flatMap(r => r.members).map(p => [p.wxid, p]));
  for (const p of members.data?.items ?? []) known.set(p.wxid, p);
  const present = new Set((members.data?.items ?? []).map(p => p.wxid));
  function choose(team: string, id: string) {
    const ids = draft[room]?.[team] ?? [];
    setDraft({ ...draft, [room]: { ...draft[room], [team]: ids.includes(id) ? ids.filter(x => x !== id) : [...ids, id] } });
    setDirty(true); setMessage(''); setError('');
  }
  function clear(team: string) {
    setDraft({ ...draft, [room]: { ...draft[room], [team]: [] } });
    setDirty(true); setMessage(''); setError('');
  }
  async function save() {
    if (!saved) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await request<Settings>('mentions', {
        revision: saved.revision, account: members.data?.account ?? saved.account,
        selections: rooms.flatMap(r => saved.teams.map(t => ({ room_id: r.room_id, team_id: t.id, wxids: draft[r.room_id]?.[t.id] ?? [] }))),
      });
      setSaved(result); setDraft(initialDraft(result));
      await config.refetch(); setDirty(false);
      setMessage('@ 选择已保存，仅影响尚未提交的文字通知；未发送测试消息。');
    } catch (e) { setError(e instanceof Error ? e.message : '@ 设置保存失败'); }
    finally { setBusy(false); }
  }
  const kindLabels: Record<string, string> = { call: '配送通话', failure: '无法配送反馈', first_call: '首拨提醒', analysis: 'AI 复盘' };
  const hasChoices = rooms.some(r => Object.values(draft[r.room_id] ?? {}).some(ids => ids.length > 0));
  return <section className="delivery-mentions" aria-labelledby="delivery-mentions-title">
    <header><h3 id="delivery-mentions-title">按业务组选择 @ 人员</h3><span>{dirty ? '有未保存的选择' : hasChoices ? '已保存 @ 选择' : '默认不 @，由你勾选'}</span></header>
    <p className="delivery-help">从通知微信群的真实成员中选择，可多选。只 @ 当前订单所属业务组对应的人，不会 @ 所有人；图片继续引用带 @ 的文字通知。</p>
    {saved?.kinds && <p className="delivery-help">适用：{saved.kinds.map(k => kindLabels[k] ?? k).join('、')}。</p>}
    {!rooms.length && <p>请先在上方保存通知目标微信群，再选择各组 @ 人员。</p>}
    {disabled && <p role="status">上方通知设置有未保存改动，请先保存后再编辑 @ 人员。</p>}
    {config.error && <p role="alert">{config.error.message}</p>}
    {rooms.length > 0 && <>
      <div className="delivery-mention-toolbar">
        <label>通知微信群<select aria-label="@ 人员所属微信群" value={room} disabled={busy || disabled} onChange={e => { setRoom(e.target.value); setSearch(''); }}>{rooms.map(r => <option value={r.room_id} key={r.room_id}>{r.name}</option>)}</select></label>
        <label>筛选成员<input type="search" aria-label="搜索可 @ 的群成员" placeholder="群昵称、姓名或微信 ID" value={search} onChange={e => setSearch(e.target.value)} /></label>
        <button type="button" disabled={busy || members.isFetching || !room} onClick={() => void members.refetch()}>刷新群成员</button>
      </div>
      {members.isFetching && <p role="status">正在读取当前微信群成员…</p>}
      {members.error && <p role="alert">{members.error.message}。不会用其他群成员替代。</p>}
      <div className="delivery-mention-grid">
        {(saved?.teams ?? []).map(team => {
          const ids = draft[room]?.[team.id] ?? [];
          const options = [...new Map([...(members.data?.items ?? []), ...ids.filter(id => !present.has(id)).map(id => known.get(id) ?? { wxid: id, name: id })].map(p => [p.wxid, p])).values()];
          const filtered = options.filter(p => `${p.name} ${p.wxid}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
          return <fieldset key={team.id} disabled={busy || disabled} className="delivery-mention-team">
            <legend>{team.name}</legend><div className="delivery-mention-count"><span>已选 {ids.length} 人</span><button type="button" disabled={!ids.length} onClick={() => clear(team.id)}>清空{team.name}</button></div>
            <div className="delivery-mention-people" aria-label={`${team.name}可选人员`}>
              {filtered.map(p => <label key={p.wxid} className="delivery-mention-person"><input type="checkbox" aria-label={`${team.name} @ ${p.name}（${p.wxid}）`} checked={ids.includes(p.wxid)} disabled={!ids.includes(p.wxid) && (!present.has(p.wxid) || members.isFetching)} onChange={() => choose(team.id, p.wxid)} /><span><strong>{p.name}{p.wxid === members.data?.account ? '（当前发送账号）' : ''}</strong><small>{p.wxid}</small>{members.isSuccess && !present.has(p.wxid) && <em>已不在当前群成员列表，请取消勾选</em>}</span></label>)}
              {!members.isFetching && !filtered.length && <p className="delivery-help">没有匹配的成员。</p>}
            </div>
            <p className="delivery-mention-preview">{ids.length ? ids.map(id => '@' + (known.get(id)?.name ?? id)).join(' ') : '未选择，本组通知不 @ 任何人'}</p>
          </fieldset>;
        })}
      </div>
      <div className="delivery-controls delivery-save-actions"><button type="button" className="delivery-primary" disabled={!saved || busy || disabled || !dirty} onClick={() => void save()}>{busy ? '正在保存…' : '保存各组 @ 人员'}</button><span className="delivery-help">仅保存选择，不立即发送通知，不改历史消息。</span></div>
    </>}
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
  </section>;
}
