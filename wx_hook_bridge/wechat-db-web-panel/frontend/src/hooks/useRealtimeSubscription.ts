import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, API_BASE, RealtimeStatusResponse } from "../lib/api";
import { parseRealtimeEvent, queryScopesForRealtimeEvent, RealtimeEvent, snapshotTimeFromRealtimeEvent } from "../lib/realtime";

export type RealtimeConnectionState = "connecting" | "connected" | "disconnected" | "unsupported";
const CURSOR_STORAGE_KEY = "wechat-realtime-cursor";

function readResumeCursor(): string | undefined {
  try {
    return window.sessionStorage.getItem(CURSOR_STORAGE_KEY) || undefined;
  } catch {
    return undefined;
  }
}

function storeResumeCursor(cursor: string) {
  try {
    window.sessionStorage.setItem(CURSOR_STORAGE_KEY, cursor);
  } catch {
    // The subscription still works when storage is disabled.
  }
}

export function useRealtimeSubscription() {
  const queryClient = useQueryClient();
  const [connectionState, setConnectionState] = useState<RealtimeConnectionState>(() => typeof EventSource === "undefined" ? "unsupported" : "connecting");
  const [lastEvent, setLastEvent] = useState<RealtimeEvent>();
  const [eventSnapshotTime, setEventSnapshotTime] = useState<string>();
  const lastProcessedCursor = useRef<string>();
  const resumeCursor = useRef<string | undefined>(readResumeCursor());
  const statusQuery = useQuery({
    queryKey: ["realtime-status"],
    queryFn: ({ signal }) => api.realtimeStatus(signal),
    staleTime: 10_000,
    refetchInterval: 30_000,
    retry: 1,
  });

  useEffect(() => {
    if (typeof EventSource === "undefined") {
      setConnectionState("unsupported");
      return undefined;
    }

    setConnectionState("connecting");
    const eventsUrl = new URL(`${API_BASE}/realtime/events`, window.location.origin);
    if (resumeCursor.current) eventsUrl.searchParams.set("after", resumeCursor.current);
    const source = new EventSource(eventsUrl.toString());

    source.onopen = () => setConnectionState("connected");
    source.onerror = () => {
      // EventSource retains the connection and retries automatically with the server retry interval.
      setConnectionState("disconnected");
    };

    const handleEvent = (message: MessageEvent<string>) => {
      const event = parseRealtimeEvent(message.data);
      if (!event) return;
      if (message.lastEventId && message.lastEventId === lastProcessedCursor.current) return;
      if (message.lastEventId) {
        lastProcessedCursor.current = message.lastEventId;
        resumeCursor.current = message.lastEventId;
        storeResumeCursor(message.lastEventId);
      }
      setLastEvent(event);
      const snapshotTime = snapshotTimeFromRealtimeEvent(event);
      if (snapshotTime) setEventSnapshotTime(snapshotTime);

      queryClient.setQueryData<RealtimeStatusResponse>(["realtime-status"], (current) => ({
        ...(current ?? {}),
        last_event_id: event.event_id,
        last_event_type: event.event_type,
        ...(snapshotTime ? { snapshot_time: snapshotTime } : {}),
      }));

      const scopes = new Set(queryScopesForRealtimeEvent(event.event_type));
      if (scopes.size) {
        void queryClient.invalidateQueries({
          predicate: (query) => scopes.has(String(query.queryKey[0] ?? "")),
          refetchType: "active",
        });
      }
    };

    source.onmessage = handleEvent;
    const namedEvents = [
      "realtime.message", "realtime.reset",
      "message", "contact", "resource", "sns.updated", "database_updated", "reset",
    ] as const;
    namedEvents.forEach((eventName) => source.addEventListener(eventName, handleEvent as EventListener));

    return () => {
      namedEvents.forEach((eventName) => source.removeEventListener(eventName, handleEvent as EventListener));
      source.close();
    };
  }, [queryClient]);

  return {
    connectionState,
    lastEvent,
    serverStatus: statusQuery.data,
    snapshotTime: eventSnapshotTime || statusQuery.data?.snapshot_time,
    statusError: statusQuery.error,
  };
}
