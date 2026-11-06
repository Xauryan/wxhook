import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { DeliveryDialogues } from './DeliveryDialogues';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
function fixture(failed = false) {
  const posts: { path: string; payload: Record<string, unknown> }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => {
    if (options?.method === 'POST') {
      posts.push({ path, payload: JSON.parse(String(options.body)) });
      return { ok: !failed, json: async () => failed ? { detail: '固定模板生成失败' } : { items: [{ image_id: 'image', room_id: 'fixture@chatroom', room_name: '模拟配送群', title: '外呼通话记录', body: '', feedback_body: '【无法配送反馈】订单 #10648\n所属组：模拟组｜外呼员：小周\n客户电话：13800001234', feedback_status: 'pending', status: 'pending', context_status: 'not_required', reference: null }] } };
    }
    return { ok: true, json: async () => path.includes('/dialogues/references?') ? { items: [] } : path.endsWith('/notifications') ? { config: { target_groups: [{ room_id: 'fixture@chatroom', name: '模拟配送群' }] } } : { items: [{ id: 'image', call_id: 7, title: '外呼通话记录', status: 'ready', height: 4296, width: 900, receipts: [] }] } };
  }));
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><DeliveryDialogues caseId={1} calls={[{ id: 7, call_time: '2026-09-19', delivery_transcript: { status: 'succeeded' } }]} /></QueryClientProvider>);
  return posts;
}
it('previews and downloads without sending; simulation is explicit and real send needs confirmation', async () => {
  const posts = fixture();
  await screen.findByText(/图片生成成功/);
  fireEvent.click(screen.getByRole('button', { name: '预览长图' }));
  expect(screen.getByRole('img').getAttribute('src')).toContain('/dialogues/image/image');
  expect(screen.getByRole('link', { name: '下载长图' }).getAttribute('href')).toContain('download=true');
  expect(posts).toHaveLength(0);
  fireEvent.click(screen.getByRole('checkbox', { name: /外呼通话记录/ }));
  fireEvent.click(screen.getByRole('checkbox', { name: '模拟配送群' }));
  expect(screen.getByRole('button', { name: '发送所选长图到所选群' })).toBeDisabled();
  expect(screen.queryByText('查找原消息')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '预览群消息 / 模拟检查' }));
  await screen.findByText('模拟检查通过，未调用微信发送接口');
  expect(posts[0].payload).toEqual({ image_ids: ['image'], room_ids: ['fixture@chatroom'], simulate: true });
  expect(screen.getByText(/客户电话：13800001234/)).toBeInTheDocument();
  expect(document.querySelectorAll('.delivery-message-plan pre')).toHaveLength(1);
  expect(screen.getByText(/取得原通知真实消息编号后发送原生引用/)).toBeInTheDocument();
  expect(screen.getByText(/查不到则暂缓，不发普通图片/)).toBeInTheDocument();
  vi.spyOn(window, 'confirm').mockReturnValue(false);
  fireEvent.click(screen.getByRole('button', { name: '发送所选长图到所选群' }));
  expect(posts).toHaveLength(1);
});
it('shows generation failures and submits explicit regeneration only once', async () => {
  const posts = fixture(true);
  await screen.findByText(/图片生成成功/);
  fireEvent.click(screen.getByRole('button', { name: '重新生成' }));
  expect(screen.getByRole('button', { name: '重新生成' })).toBeDisabled();
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('固定模板生成失败'));
  expect(posts).toHaveLength(1);
  expect(posts[0].payload).toEqual({ call_id: 7, regenerate: true });
});
