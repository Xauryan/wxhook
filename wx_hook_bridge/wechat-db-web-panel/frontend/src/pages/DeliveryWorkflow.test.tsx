import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { DeliveryWorkflow } from "./DeliveryWorkflow";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function fixture() {
  const posts: { url: string; body: Record<string, unknown> }[] = [];
  const detail = { id: 1, order_id: 42, status: "open", review_status: "pending", owner_user_id: null, owner_name: null, due_at: null, callback_at: null, resolution: null, version: 3, analysis_status: "succeeded", analysis_error: null, reported_reason: "Fixture reason", selected_calls: null,
    analysis: { summary: "两侧费用需要核对", findings: [{ issue: "费用尚未确认", kind: "fact", refs: ["call:0"] }], evidence_gaps: [], next_actions: ["联系客户核实"], recoverability: "possible", references: { "call:0": { role: "outbound", text: "这项费用还需要确认", begin_ms: 1000, end_ms: 2000 } } },
    candidates: { items: [{ id: 10, user_name: "Fixture agent", role: "outbound", call_time: "2026-09-20T10:00:00+08:00", recording_available: true, full_text: "Fixture text" }, { id: 11, user_name: "Fixture driver", role: "delivery", call_time: "2026-09-20T11:00:00+08:00", recording_available: true, full_text: "Fixture text" }], has_more: false },
    history: { assignments: [{ id: 1, staff_name: "Fixture driver", valid_from: "2026-09-20T09:00:00+08:00", valid_to: null, baseline: true }], events: [] }, actions: [] };
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/dialogues')) return { ok: true, json: async () => ({ items: [] }) };
    if (url.endsWith('/notifications')) return { ok: true, json: async () => ({ config: { target_groups: [] } }) };
    if (options?.method === "POST") posts.push({ url, body: JSON.parse(String(options.body)) });
    const data = url.endsWith("/workflow/status") ? { config: { auto_analysis: true }, asr_configured: true, llm_configured: true } : url.endsWith("/cases/owners") ? { items: [{ id: 2, real_name: "Fixture owner" }] } : url.endsWith("/cases") ? { items: [detail], has_more: false } : detail;
    return { ok: true, json: async () => data };
  }));
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><DeliveryWorkflow initialCaseId={1} /></QueryClientProvider>);
  return posts;
}

it("renders dual-party report and source evidence without automatic writes", async () => {
  const posts = fixture();
  expect(await screen.findByText("两侧费用需要核对")).toBeInTheDocument();
  fireEvent.click(screen.getByText("AI 判断，待核实：费用尚未确认"));
  expect(screen.getByText("这项费用还需要确认")).toBeVisible();
  expect(screen.getByText("待人工复核")).toBeInTheDocument();
  expect(posts).toHaveLength(0);
});

it("saves workflow with its loaded version and explicit Beijing timestamps", async () => {
  const posts = fixture();
  await screen.findByText("两侧费用需要核对");
  fireEvent.change(screen.getByLabelText("负责人"), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("处理期限"), { target: { value: "2026-09-20T15:00" } });
  fireEvent.change(screen.getByLabelText("处理状态"), { target: { value: "in_progress" } });
  fireEvent.click(screen.getByRole("button", { name: "保存处理记录" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toMatchObject({ version: 3, owner_user_id: 2, status: "in_progress", due_at: "2026-09-20T07:00:00.000Z" });
});

it("does not submit paid analysis if the confirmation is cancelled", async () => {
  const posts = fixture(); vi.spyOn(window, "confirm").mockReturnValue(false);
  await screen.findByText("两侧费用需要核对");
  fireEvent.click(screen.getByText("录音与转写"));
  fireEvent.change(screen.getByLabelText('录音 11 归属'), { target: { value: 'delivery' } });
  fireEvent.click(screen.getByRole("button", { name: "确认相关录音并处理" }));
  expect(window.confirm).toHaveBeenCalled();
  expect(posts).toHaveLength(0);
});

it('never defaults every associated recording into a paid task', async () => {
  const posts = fixture(); vi.spyOn(window, 'confirm').mockReturnValue(true);
  await screen.findByText('两侧费用需要核对');
  expect(screen.getByLabelText('录音 10 归属')).toHaveValue('');
  expect(screen.getByLabelText('录音 11 归属')).toHaveValue('');
  fireEvent.click(screen.getByRole('button', { name: '确认相关录音并处理' }));
  expect(window.confirm).not.toHaveBeenCalled();
  expect(posts).toHaveLength(0);
});

it('submits only explicitly chosen business purpose, not all associated calls', async () => {
  const posts = fixture(); vi.spyOn(window, 'confirm').mockReturnValue(true);
  await screen.findByText('两侧费用需要核对');
  fireEvent.change(screen.getByLabelText('录音 11 归属'), { target: { value: 'delivery' } });
  fireEvent.click(screen.getByRole('button', { name: '确认相关录音并处理' }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body.selected_calls).toEqual([{ id: 11, role: 'delivery', purpose: 'delivery_failure' }]);
});
