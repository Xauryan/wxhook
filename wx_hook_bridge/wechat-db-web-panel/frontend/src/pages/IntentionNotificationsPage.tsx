import { FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BellRing,
  Check,
  CircleAlert,
  Clock3,
  Eye,
  EyeOff,
  History,
  RefreshCw,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  UsersRound,
  Wifi,
} from "lucide-react";
import { Card, EmptyState, ErrorState, LoadingState } from "../components/ui";
import {
  api,
  IntentionNotificationConfigInput,
  IntentionNotificationGroup,
} from "../lib/api";
import { formatDateTime } from "../lib/format";

type Draft = {
  enabled: boolean;
  consumerId: string;
  token: string;
  targetGroups: IntentionNotificationGroup[];
  startupPolicy: "new_only" | "include_pending";
};

type PreviewMode = "quick" | "stage" | "closing";

const runtimeLabels: Record<string, string> = {
  connected: "WSS 已连接",
  connecting: "正在连接",
  fallback: "REST 备用中",
  error: "连接异常",
  disabled: "通知未开启",
  not_ready: "配置未完成",
  needs_token: "缺少令牌",
  needs_groups: "未选择群",
  needs_startup_policy: "请选择启动策略",
  stopped: "服务已停止",
};

const eventLabels: Record<string, string> = {
  received: "已落盘",
  sending: "发送中",
  sent: "已发送",
  waiting_retry: "等待重试",
  skipped: "已跳过",
};

const defaultSchedule = [
  { key: "stage_1200", label: "12:00", kind: "stage" as const },
  { key: "stage_1600", label: "16:00", kind: "stage" as const },
  { key: "stage_1830", label: "18:30", kind: "stage" as const },
  { key: "closing_2100", label: "21:00", kind: "closing" as const },
];

function exampleMessage(mode: PreviewMode): string {
  if (mode === "closing") {
    return [
      "【今日收官｜21:00】",
      "数据截至 21:00",
      "全场累计 68 单",
      "在岗 10 人｜已破零 9 人｜待破零 1 人",
      "",
      "【呼叫中心四组】",
      "今日 21 单｜昨日同期 17 单｜+24%",
      "已破零 9/10 人｜待破零 1 人",
      "在岗人均 2.1 单｜最高 4 单",
      "4 单：王静",
      "3 单：李敏、陈芳",
      "2 单：赵宁、周倩、刘欣",
      "1 单：吴悦、孙晴、杨帆",
      "0 单：何佳",
      "明日优先关注：何佳",
      "",
      "在岗口径：拨打不同号码 > 10",
    ].join("\n");
  }
  if (mode === "stage") {
    return [
      "【阶段战报｜18:30】",
      "呼叫中心四组",
      "",
      "今日 15 单｜昨日同期 12 单｜+25%",
      "已破零 8/10 人｜破零率 80%",
      "",
      "【组内分布】",
      "2 单：王静、李敏、陈芳、赵宁",
      "      周倩、刘欣、吴悦",
      "1 单：孙晴",
      "0 单：杨帆、何佳",
      "",
      "【本阶段变化】",
      "周倩 0→2｜新破零",
      "王静 1→2｜升至并列第 1",
      "孙晴 0→1｜新破零",
      "",
      "待破零：杨帆、何佳",
      "全场累计 48 单",
      "在岗口径：拨打号码 > 10",
    ].join("\n");
  }
  return [
    "【新单｜呼叫中心四组】",
    "王静 +1｜个人 2 单",
    "18:17 录单",
    "",
    "小组 15 单｜昨日同期 12 单",
    "已破零 8/10 人｜待破零 2 人",
    "",
    "本次变化：1→2，并列第 1",
    "全场累计 48 单",
  ].join("\n");
}

function epochLabel(value?: number | null): string {
  if (!value) return "尚未发送";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function IntentionNotificationsPage() {
  const queryClient = useQueryClient();
  const statusQuery = useQuery({
    queryKey: ["intention-notifications"],
    queryFn: ({ signal }) => api.intentionNotifications(signal),
    refetchInterval: 3_000,
  });
  const groupsQuery = useQuery({
    queryKey: ["intention-notification-groups"],
    queryFn: ({ signal }) => api.intentionNotificationGroups(signal),
    retry: 1,
  });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [dirty, setDirty] = useState(false);
  const [groupSearch, setGroupSearch] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [saved, setSaved] = useState(false);
  const [previewMode, setPreviewMode] = useState<PreviewMode>("quick");

  useEffect(() => {
    if (!statusQuery.data || dirty) return;
    setDraft({
      enabled: statusQuery.data.config.enabled,
      consumerId: statusQuery.data.config.consumer_id,
      token: "",
      targetGroups: statusQuery.data.config.target_groups,
      startupPolicy: statusQuery.data.config.startup_policy,
    });
  }, [statusQuery.data, dirty]);

  const mutation = useMutation({
    mutationFn: (payload: IntentionNotificationConfigInput) =>
      api.configureIntentionNotifications(payload),
    onSuccess: (data) => {
      queryClient.setQueryData(["intention-notifications"], data);
      setDraft({
        enabled: data.config.enabled,
        consumerId: data.config.consumer_id,
        token: "",
        targetGroups: data.config.target_groups,
        startupPolicy: data.config.startup_policy,
      });
      setDirty(false);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2_000);
    },
  });

  const groupOptions = useMemo(() => {
    const byId = new Map<string, IntentionNotificationGroup>();
    for (const group of groupsQuery.data?.items ?? []) byId.set(group.room_id, group);
    for (const group of draft?.targetGroups ?? []) {
      if (!byId.has(group.room_id)) byId.set(group.room_id, group);
    }
    const query = groupSearch.trim().toLocaleLowerCase();
    return [...byId.values()].filter((group) =>
      !query || group.name.toLocaleLowerCase().includes(query) || group.room_id.toLocaleLowerCase().includes(query)
    );
  }, [draft?.targetGroups, groupSearch, groupsQuery.data?.items]);

  if (statusQuery.isLoading || !draft) {
    return <LoadingState label="正在读取意向单通知配置" />;
  }
  if (statusQuery.isError || !statusQuery.data) {
    return <ErrorState error={statusQuery.error} retry={() => statusQuery.refetch()} />;
  }

  const form = draft;
  const { config, runtime, today, recent } = statusQuery.data;
  const runtimeLabel = runtimeLabels[runtime.state] ?? runtime.state;
  const runtimeTone = runtime.state === "connected"
    ? "ok"
    : runtime.state === "fallback" || runtime.state === "connecting"
      ? "warn"
      : runtime.state === "disabled"
        ? "idle"
        : "bad";
  const selectedIds = new Set(form.targetGroups.map((group) => group.room_id));
  const preview = exampleMessage(previewMode);
  const snapshotState = today.team_snapshot_state
    ?? (today.team_snapshot_ready ? "ready" : "missing");
  const snapshotLabel = snapshotState === "ready"
    ? "已接入"
    : snapshotState === "no_events"
      ? "等待今日首单"
      : "安全降级";
  const scheduleSlots = statusQuery.data.schedule?.slots ?? defaultSchedule;
  const scheduleReports = statusQuery.data.schedule?.reports ?? [];
  const reportsBySlot = new Map<string, typeof scheduleReports>();
  for (const report of scheduleReports) {
    reportsBySlot.set(report.slot, [...(reportsBySlot.get(report.slot) ?? []), report]);
  }
  const runsBySlot = new Map(
    (statusQuery.data.schedule?.runs ?? []).map((run) => [run.slot, run]),
  );
  const tokenReady = config.token_configured || Boolean(form.token.trim());
  const canSave = Boolean(
    form.consumerId.trim()
    && (!form.enabled || (tokenReady && form.targetGroups.length > 0))
  );

  function updateDraft(update: Partial<Draft>) {
    setDraft((current) => current ? { ...current, ...update } : current);
    setDirty(true);
    setSaved(false);
  }

  function toggleGroup(group: IntentionNotificationGroup) {
    const next = selectedIds.has(group.room_id)
      ? form.targetGroups.filter((item) => item.room_id !== group.room_id)
      : [...form.targetGroups, group];
    updateDraft({ targetGroups: next });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSave || mutation.isPending) return;
    mutation.mutate({
      enabled: form.enabled,
      consumer_id: form.consumerId.trim(),
      target_groups: form.targetGroups,
      api_token: form.token.trim() || null,
      startup_policy: form.startupPolicy,
    });
  }

  return (
    <div className="page intention-notification-page">
      <header className="intention-hero">
        <div className="intention-hero-copy">
          <span className="intention-kicker"><BellRing size={15} /> ROBOT NOTICE</span>
          <h1>意向单通知</h1>
          <p>每单即时提示个人变化，阶段节点展开全组破零进度与成员分布。</p>
        </div>
        <div className={`intention-runtime ${runtimeTone}`} role="status" aria-live="polite">
          <i />
          <div>
            <strong>{runtimeLabel}</strong>
            <span>{runtime.transport ? `当前通道 ${runtime.transport.toUpperCase()}` : "等待启用配置"}</span>
          </div>
        </div>
      </header>

      <section className="intention-metrics" aria-label="今日通知概况">
        <article>
          <span>今日意向单</span>
          <strong>{today.total}</strong>
          <small>{today.date}</small>
        </article>
        <article>
          <span>发送成功</span>
          <strong>{today.sent}</strong>
          <small>全部目标群完成</small>
        </article>
        <article>
          <span>历史已跳过</span>
          <strong>{today.skipped}</strong>
          <small>不发群但参与累计</small>
        </article>
        <article>
          <span>本地待发</span>
          <strong>{today.pending}</strong>
          <small>已可靠落盘</small>
        </article>
        <article className={today.retrying ? "attention" : ""}>
          <span>等待重试</span>
          <strong>{today.retrying}</strong>
          <small>{today.retrying ? "请检查微信状态" : "当前无失败任务"}</small>
        </article>
      </section>

      <div className="intention-workspace">
        <Card
          className="intention-config-card"
          title={<><Settings2 />通知配置</>}
          action={<span className={`intention-token-state ${tokenReady ? "ready" : ""}`}>{tokenReady ? "令牌已配置" : "令牌未配置"}</span>}
        >
          <form className="intention-config-form" onSubmit={submit}>
            <label className="intention-enable-row">
              <span>
                <strong>开启自动通知</strong>
                <small>开启后才会从远端领取意向单</small>
              </span>
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(event) => updateDraft({ enabled: event.target.checked })}
                aria-label="开启自动通知"
              />
              <i aria-hidden="true"><b /></i>
            </label>

            {draft.enabled && !config.enabled && (
              <fieldset className="intention-startup-policy">
                <legend>启动时处理</legend>
                <p>请选择是否发送本次启用时间以前积压的意向单。</p>
                <div>
                  <label className={draft.startupPolicy === "new_only" ? "selected" : ""}>
                    <input
                      type="radio"
                      name="startup-policy"
                      value="new_only"
                      checked={draft.startupPolicy === "new_only"}
                      onChange={() => updateDraft({ startupPolicy: "new_only" })}
                    />
                    <span><ShieldCheck size={18} /></span>
                    <strong>仅发送新单</strong>
                    <small>启用前的单只统计，不发群消息</small>
                    <b>推荐</b>
                  </label>
                  <label className={draft.startupPolicy === "include_pending" ? "selected" : ""}>
                    <input
                      type="radio"
                      name="startup-policy"
                      value="include_pending"
                      checked={draft.startupPolicy === "include_pending"}
                      onChange={() => updateDraft({ startupPolicy: "include_pending" })}
                    />
                    <span><History size={18} /></span>
                    <strong>补发积压单</strong>
                    <small>截止现在的待发单会依次发送</small>
                  </label>
                </div>
              </fieldset>
            )}

            {draft.enabled && config.enabled && (
              <div className="intention-active-policy">
                <ShieldCheck size={16} />
                <span>
                  本次启动：{config.startup_policy === "new_only" ? "仅发送启用后的新单" : "补发启动时的积压单"}
                </span>
              </div>
            )}

            <div className="intention-field">
              <label htmlFor="intention-consumer-id">消费者标识</label>
              <input
                id="intention-consumer-id"
                value={draft.consumerId}
                onChange={(event) => updateDraft({ consumerId: event.target.value })}
                autoComplete="off"
                spellCheck={false}
                aria-describedby="consumer-id-help"
              />
              <small id="consumer-id-help">保持稳定，用于租约归属和事件去重。</small>
            </div>

            <div className="intention-field">
              <label htmlFor="intention-api-token">鉴权令牌</label>
              <span className="intention-secret-control">
                <input
                  id="intention-api-token"
                  type={showToken ? "text" : "password"}
                  value={draft.token}
                  onChange={(event) => updateDraft({ token: event.target.value })}
                  placeholder={config.token_configured ? "已配置，留空保持不变" : "输入机器人消费令牌"}
                  autoComplete="new-password"
                />
                <button
                  type="button"
                  onClick={() => setShowToken((current) => !current)}
                  aria-label={showToken ? "隐藏鉴权令牌" : "显示鉴权令牌"}
                >
                  {showToken ? <EyeOff size={17} /> : <Eye size={17} />}
                </button>
              </span>
              <small>令牌只保存在本机缓存库，页面不会回显。</small>
            </div>

            <fieldset className="intention-group-fieldset">
              <div className="intention-group-heading">
                <div>
                  <legend>目标微信群</legend>
                  <small>已选择 {draft.targetGroups.length} 个群</small>
                </div>
                <button type="button" onClick={() => groupsQuery.refetch()} disabled={groupsQuery.isFetching} aria-label="刷新微信群列表">
                  <RefreshCw size={16} className={groupsQuery.isFetching ? "spin" : ""} />
                </button>
              </div>
              <div className="intention-group-search">
                <Search size={16} />
                <input
                  value={groupSearch}
                  onChange={(event) => setGroupSearch(event.target.value)}
                  placeholder="搜索群名称"
                  aria-label="搜索微信群"
                />
              </div>
              <div className="intention-group-list">
                {groupsQuery.isLoading ? (
                  <span className="intention-group-note">正在读取微信群列表</span>
                ) : groupOptions.length ? groupOptions.map((group) => (
                  <label key={group.room_id} className={selectedIds.has(group.room_id) ? "selected" : ""}>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(group.room_id)}
                      onChange={() => toggleGroup(group)}
                    />
                    <span className="intention-group-check"><Check size={14} /></span>
                    <span>
                      <strong>{group.name}</strong>
                      <small>{group.room_id}</small>
                    </span>
                  </label>
                )) : (
                  <span className="intention-group-note">{groupsQuery.isError ? "微信群读取失败，请确认桥接已启动" : "没有匹配的微信群"}</span>
                )}
              </div>
            </fieldset>

            {mutation.isError && (
              <div className="intention-form-error" role="alert">
                <CircleAlert size={16} />
                <span>{mutation.error instanceof Error ? mutation.error.message : "配置保存失败"}</span>
              </div>
            )}
            <div className="intention-form-actions">
              <span>{saved ? "配置已保存" : dirty ? "有尚未保存的修改" : "配置已同步"}</span>
              <button className="green-button" type="submit" disabled={!canSave || mutation.isPending}>
                <Send size={16} />
                {mutation.isPending ? "正在保存" : draft.enabled ? "保存并启动" : "保存配置"}
              </button>
            </div>
          </form>
        </Card>

        <Card className="intention-preview-card" title={<><BellRing />微信群预览</>}>
          <div className="intention-preview-tabs" role="group" aria-label="预览通报格式">
            <button
              type="button"
              className={previewMode === "quick" ? "selected" : ""}
              aria-pressed={previewMode === "quick"}
              onClick={() => setPreviewMode("quick")}
            >
              每单快报
            </button>
            <button
              type="button"
              className={previewMode === "stage" ? "selected" : ""}
              aria-pressed={previewMode === "stage"}
              onClick={() => setPreviewMode("stage")}
            >
              阶段战报
            </button>
            <button
              type="button"
              className={previewMode === "closing" ? "selected" : ""}
              aria-pressed={previewMode === "closing"}
              onClick={() => setPreviewMode("closing")}
            >
              收官战报
            </button>
          </div>
          <div className="wechat-preview">
            <div className="wechat-preview-bar">
              <span>意向单通知群</span>
              <small>
                {previewMode === "quick"
                  ? "每张新单发送"
                  : previewMode === "stage"
                    ? "每 5 单及定时发送"
                    : "每日 21:00 发送"}
              </small>
            </div>
            <div className="wechat-preview-canvas">
              <time>刚刚</time>
              <div className="wechat-message-row">
                <span className="wechat-robot-avatar"><BellRing size={18} /></span>
                <div>
                  <small>意向单机器人</small>
                  <pre>{preview}</pre>
                </div>
              </div>
            </div>
          </div>
          <section className="intention-schedule-panel" aria-labelledby="intention-schedule-title">
            <div>
              <Clock3 size={16} />
              <span>
                <strong id="intention-schedule-title">今日定时通报</strong>
                <small>北京时间 · 仅 4 个购机组</small>
              </span>
            </div>
            <ol>
              {scheduleSlots.map((slot) => {
                const reports = reportsBySlot.get(slot.label) ?? [];
                const run = runsBySlot.get(slot.label);
                const unavailableCount = run?.unavailable_groups.length ?? 0;
                const status = unavailableCount
                  ? "waiting_retry"
                  : reports.some((report) => report.status === "waiting_retry")
                    ? "waiting_retry"
                    : reports.some((report) => report.status === "sending")
                      ? "sending"
                      : reports.length && reports.every((report) => report.status === "sent")
                        ? "sent"
                        : reports.length
                          ? "received"
                          : "pending";
                const statusLabel = unavailableCount
                  ? `${unavailableCount}组待数据`
                  : status === "sent"
                    ? `${reports.length}组已发送`
                    : status === "sending"
                      ? "正在发送"
                      : status === "waiting_retry"
                        ? "等待重试"
                        : status === "received"
                          ? `${reports.length}组待发送`
                          : "待触发";
                return (
                  <li key={slot.key} className={`${slot.kind} ${status}`}>
                    <time>{slot.label}</time>
                    <span>{slot.kind === "closing" ? "收官战报" : "阶段战报"}</span>
                    <small>{statusLabel}</small>
                  </li>
                );
              })}
            </ol>
          </section>
          <div className="intention-protocol">
            <div><Wifi size={15} /><span>主通道</span><strong>WSS</strong></div>
            <div><RefreshCw size={15} /><span>备用通道</span><strong>REST</strong></div>
            <div><UsersRound size={15} /><span>在岗口径</span><strong>拨打号码 &gt; {today.on_duty_dialed_number_threshold ?? 10}</strong></div>
            <div>
              <ShieldCheck size={15} />
              <span>团队快照</span>
              <strong className={snapshotState === "ready" ? "snapshot-ready" : "snapshot-waiting"}>
                {snapshotLabel}
              </strong>
            </div>
          </div>
          {(runtime.last_error || runtime.last_schedule_error) && (
            <div className="intention-runtime-error">
              <CircleAlert size={15} />
              {runtime.last_schedule_error || runtime.last_error}
            </div>
          )}
        </Card>
      </div>

      <Card
        className="intention-history-card"
        title={<><Clock3 />最近投递</>}
        action={<span>{runtime.last_connected_at ? `最近连接 ${formatDateTime(runtime.last_connected_at)}` : "尚未连接远端"}</span>}
      >
        {recent.length ? (
          <div className="intention-history-list">
            {recent.map((item) => (
              <article key={item.event_id}>
                <span className={`intention-event-state ${item.status}`}><i />{eventLabels[item.status] ?? item.status}</span>
                <div>
                  <strong>{item.group_name}</strong>
                  <small>{item.agent_name} · 个人 {item.agent_today} · 小组 {item.group_today} · 全场 {item.overall_today}</small>
                </div>
                <code>{item.event_id}</code>
                <time>{item.sent_at_ms ? epochLabel(item.sent_at_ms) : formatDateTime(item.occurred_at)}</time>
                {(item.last_error || item.remote_ack_error) && <p>{item.last_error || item.remote_ack_error}</p>}
              </article>
            ))}
          </div>
        ) : (
          <EmptyState title="还没有投递记录" detail="选择目标群并开启通知后，新意向单会显示在这里。" />
        )}
      </Card>
    </div>
  );
}
