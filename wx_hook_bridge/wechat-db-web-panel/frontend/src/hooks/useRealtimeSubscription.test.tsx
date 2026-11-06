import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRealtimeSubscription } from "./useRealtimeSubscription";

class FakeEventSource {
  static latest?: FakeEventSource;
  readonly url: string;
  readyState = 0;
  closed = false;
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  private listeners = new Map<string, Set<EventListener>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.latest = this;
  }

  addEventListener(type: string, listener: EventListener) {
    const group = this.listeners.get(type) ?? new Set<EventListener>();
    group.add(listener);
    this.listeners.set(type, group);
  }

  removeEventListener(type: string, listener: EventListener) {
    this.listeners.get(type)?.delete(listener);
  }

  close() {
    this.closed = true;
  }

  open() {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }

  fail() {
    this.readyState = 0;
    this.onerror?.(new Event("error"));
  }

  emit(type: string, value: unknown, lastEventId = "") {
    const event = new MessageEvent(type, { data: JSON.stringify(value), lastEventId });
    if (type === "message") this.onmessage?.(event);
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }
}

function Harness() {
  const realtime = useRealtimeSubscription();
  return <div data-testid="state">{realtime.connectionState}:{realtime.lastEvent?.event_type ?? "none"}</div>;
}

describe("useRealtimeSubscription", () => {
  beforeEach(() => {
    FakeEventSource.latest = undefined;
    window.sessionStorage.clear();
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      json: async () => ({ enabled: true, watcher_running: true, snapshot_time: "2026-08-18T00:00:00+08:00" }),
    })));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("tracks connection failures and invalidates affected TanStack Query scopes", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(["profile"], { profile: {} });
    queryClient.setQueryData(["media", 40], { items: [] });
    const view = render(<QueryClientProvider client={queryClient}><Harness /></QueryClientProvider>);
    const source = FakeEventSource.latest!;

    expect(source.url).toContain("/api/realtime/events");
    expect(screen.getByTestId("state")).toHaveTextContent("connecting:none");
    act(() => source.open());
    expect(screen.getByTestId("state")).toHaveTextContent("connected:none");

    act(() => source.emit("realtime.message", {
      event_id: "native-message-id",
      event_type: "message.received",
      payload: { snapshot_time: "2026-08-18T00:01:00+08:00" },
    }, "17"));
    await waitFor(() => expect(screen.getByTestId("state")).toHaveTextContent("connected:message.received"));
    expect(queryClient.getQueryState(["media", 40])?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(["profile"])?.isInvalidated).toBe(true);
    expect(window.sessionStorage.getItem("wechat-realtime-cursor")).toBe("17");

    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    act(() => source.emit("realtime.reset", {
      event_id: "realtime-reset",
      event_type: "realtime.reset",
      payload: { cursor: 18 },
    }, "18"));
    act(() => source.emit("realtime.reset", {
      event_id: "realtime-reset",
      event_type: "realtime.reset",
      payload: { cursor: 19 },
    }, "19"));
    await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(2));
    expect(window.sessionStorage.getItem("wechat-realtime-cursor")).toBe("19");

    act(() => source.fail());
    expect(screen.getByTestId("state")).toHaveTextContent("disconnected:realtime.reset");
    expect(source.closed).toBe(false);
    view.unmount();
    expect(source.closed).toBe(true);

    const resumedClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const resumedView = render(<QueryClientProvider client={resumedClient}><Harness /></QueryClientProvider>);
    expect(FakeEventSource.latest?.url).toContain("after=19");
    resumedView.unmount();
  });
});
