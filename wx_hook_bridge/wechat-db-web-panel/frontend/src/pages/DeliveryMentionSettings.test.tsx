import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { DeliveryMentionSettings } from './DeliveryMentionSettings';

const rooms = [{ room_id: 'first@chatroom', name: '通知群甲' }, { room_id: 'second@chatroom', name: '通知群乙' }];
const teams = [{ id: '2', name: '呼叫中心一组' }, { id: '3', name: '呼叫中心二组' }, { id: '4', name: '呼叫中心四组' }, { id: '5', name: '润汇外呼小组' }];
type Row = { room_id: string; team_id: string; wxids: string[] };

function backend(failSave = false) {
  let config = { revision: 0, account: null as string | null, teams, kinds: ['call', 'failure'], selections: [] as { room_id: string; team_id: string; members: { wxid: string; name: string }[] }[] };
  const posts: { revision: number; account: string; selections: Row[] }[] = [];
  const people = (room: string) => room === rooms[0].room_id ? [{ wxid: 'wxid_a', name: '甲' }, { wxid: 'wxid_b', name: '乙' }] : [{ wxid: 'wxid_c', name: '丙' }];
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === 'POST') {
      const body = JSON.parse(String(options.body)); posts.push(body);
      if (failSave) return { ok: false, json: async () => ({ detail: '成员已退出，请刷新' }) };
      config = { ...config, revision: config.revision + 1, account: body.account,
        selections: body.selections.filter((r: Row) => r.wxids.length).map((r: Row) => ({ room_id: r.room_id, team_id: r.team_id,
          members: people(r.room_id).filter(p => r.wxids.includes(p.wxid)) })) };
    }
    if (url.includes('mention-members')) {
      const room = new URL(url, 'http://localhost').searchParams.get('room_id')!;
      return { ok: true, json: async () => ({ room_id: room, account: 'wxid_self', items: people(room) }) };
    }
    return { ok: true, json: async () => config };
  }));
  return posts;
}

function show(options: { rooms?: typeof rooms; disabled?: boolean } = {}) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <DeliveryMentionSettings rooms={options.rooms ?? rooms} disabled={options.disabled} />
  </QueryClientProvider>);
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('shows exactly the four business teams with nobody preselected', async () => {
  const posts = backend(); show();
  for (const team of teams) expect(await screen.findByRole('group', { name: team.name })).toBeInTheDocument();
  await screen.findByRole('checkbox', { name: '呼叫中心一组 @ 甲（wxid_a）' });
  for (const checkbox of screen.getAllByRole('checkbox')) expect(checkbox).not.toBeChecked();
  expect(screen.queryByRole('group', { name: '呼叫中心三组' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '保存各组 @ 人员' })).toBeDisabled();
  expect(posts).toEqual([]);
});

it('saves independent selections by team using real member ids, without sending', async () => {
  const posts = backend(); show();
  fireEvent.click(await screen.findByRole('checkbox', { name: '呼叫中心一组 @ 甲（wxid_a）' }));
  fireEvent.click(screen.getByRole('checkbox', { name: '呼叫中心二组 @ 乙（wxid_b）' }));
  expect(within(screen.getByRole('group', { name: '呼叫中心一组' })).getByText('已选 1 人')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '保存各组 @ 人员' }));
  await screen.findByText(/@ 选择已保存/);
  expect(posts).toHaveLength(1);
  expect(posts[0].selections.find(r => r.room_id === rooms[0].room_id && r.team_id === '2')?.wxids).toEqual(['wxid_a']);
  expect(posts[0].selections.find(r => r.room_id === rooms[0].room_id && r.team_id === '3')?.wxids).toEqual(['wxid_b']);
  expect(posts[0].selections.find(r => r.room_id === rooms[0].room_id && r.team_id === '4')?.wxids).toEqual([]);
  expect(screen.getByRole('checkbox', { name: '呼叫中心一组 @ 甲（wxid_a）' })).toBeChecked();
});

it('keeps per-chatroom selections separate while switching rooms', async () => {
  const posts = backend(); show();
  fireEvent.click(await screen.findByRole('checkbox', { name: '呼叫中心一组 @ 甲（wxid_a）' }));
  fireEvent.change(screen.getByRole('combobox', { name: '@ 人员所属微信群' }), { target: { value: rooms[1].room_id } });
  fireEvent.click(await screen.findByRole('checkbox', { name: '呼叫中心一组 @ 丙（wxid_c）' }));
  expect(screen.queryByRole('checkbox', { name: '呼叫中心一组 @ 甲（wxid_a）' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '保存各组 @ 人员' }));
  await screen.findByText(/@ 选择已保存/);
  expect(posts[0].selections.find(r => r.room_id === rooms[0].room_id && r.team_id === '2')?.wxids).toEqual(['wxid_a']);
  expect(posts[0].selections.find(r => r.room_id === rooms[1].room_id && r.team_id === '2')?.wxids).toEqual(['wxid_c']);
});

it('keeps choices on save failure and supports clearing one team', async () => {
  const posts = backend(true); show();
  fireEvent.click(await screen.findByRole('checkbox', { name: '呼叫中心一组 @ 甲（wxid_a）' }));
  fireEvent.click(screen.getByRole('button', { name: '保存各组 @ 人员' }));
  await screen.findByText('成员已退出，请刷新');
  expect(screen.getByRole('checkbox', { name: '呼叫中心一组 @ 甲（wxid_a）' })).toBeChecked();
  await waitFor(() => expect(screen.getByRole('button', { name: '清空呼叫中心一组' })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: '清空呼叫中心一组' }));
  expect(screen.getByRole('checkbox', { name: '呼叫中心一组 @ 甲（wxid_a）' })).not.toBeChecked();
  expect(posts).toHaveLength(1);
});

it('does not guess a destination when no notification group is saved', async () => {
  backend(); show({ rooms: [] });
  expect(await screen.findByText(/请先在上方保存通知目标微信群/)).toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
});
