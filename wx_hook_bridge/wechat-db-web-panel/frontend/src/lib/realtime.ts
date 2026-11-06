export type RealtimeEventType = "message" | "contact" | "resource" | "database_updated" | "reset";

export type RealtimeEvent = {
  event_id: string;
  event_type: RealtimeEventType | string;
  payload: Record<string, unknown>;
};

const EVENT_QUERY_SCOPES: Record<RealtimeEventType, readonly string[]> = {
  message: [
    "profile", "overview", "contacts", "contacts-graph", "media", "media-content-search",
    "contributions", "conversations", "conversation-detail", "chat-list", "chat-session", "search",
  ],
  contact: ["profile", "contacts", "contacts-graph", "chat-list", "chat-session", "search"],
  resource: ["overview", "media", "conversations", "conversation-detail", "chat-session", "search"],
  database_updated: [
    "profile", "overview", "contacts", "contacts-graph", "media", "media-content-search",
    "contributions", "conversations", "conversation-detail", "chat-list", "chat-session", "search",
    "realtime-status",
  ],
  reset: [
    "profile", "overview", "contacts", "contacts-graph", "media", "media-content-search",
    "contributions", "conversations", "conversation-detail", "chat-list", "chat-session", "search",
    "realtime-status",
  ],
};

const SNS_QUERY_SCOPES = ["sns", "sns-stats", "sns-detail", "sns-sync-status"] as const;

export function parseRealtimeEvent(data: string): RealtimeEvent | undefined {
  try {
    const value = JSON.parse(data) as Partial<RealtimeEvent>;
    if ((typeof value.event_id !== "string" && typeof value.event_id !== "number") || typeof value.event_type !== "string") return undefined;
    return {
      event_id: String(value.event_id),
      event_type: value.event_type,
      payload: value.payload && typeof value.payload === "object" && !Array.isArray(value.payload) ? value.payload : {},
    };
  } catch {
    return undefined;
  }
}

export function queryScopesForRealtimeEvent(eventType: string): readonly string[] {
  if (eventType.startsWith("message.") || eventType === "bridge.raw") return EVENT_QUERY_SCOPES.message;
  if (eventType.startsWith("contact.")) return EVENT_QUERY_SCOPES.contact;
  if (eventType.startsWith("resource.")) return EVENT_QUERY_SCOPES.resource;
  if (eventType.startsWith("sns.")) return SNS_QUERY_SCOPES;
  if (eventType === "realtime.reset") return EVENT_QUERY_SCOPES.reset;
  return EVENT_QUERY_SCOPES[eventType as RealtimeEventType] ?? [];
}

export function snapshotTimeFromRealtimeEvent(event: RealtimeEvent): string | undefined {
  const value = event.payload.snapshot_time ?? event.payload.snapshotTime;
  return typeof value === "string" && value ? value : undefined;
}
