import { useQuery } from "@tanstack/react-query";
import { CalendarDays, Clock3, ContactRound, MessageCircle, MessagesSquare } from "lucide-react";
import { useNavigate, useOutletContext, useSearchParams } from "react-router-dom";
import { CalendarHeatmap, DonutChart, TrendChart } from "../components/charts";
import { Avatar, Card, EmptyState, ErrorState, LoadingState, ProfileBanner, SectionLink } from "../components/ui";
import { api } from "../lib/api";
import { formatDateTime, formatNumber, safeText } from "../lib/format";
import type { ShellContext } from "../components/AppShell";

function firstValue(source: Record<string, number | string | null> | undefined, keys: string[]): number | string | null | undefined {
  return keys.map((key) => source?.[key]).find((value) => value !== undefined && value !== null);
}

export function OverviewPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { profile, profileResponse } = useOutletContext<ShellContext>();
  const range = searchParams.get("range") ?? "30";
  const days = range === "90" ? 90 : range === "all" ? 366 : 30;
  const query = useQuery({ queryKey: ["overview", days], queryFn: ({ signal }) => api.overview(days, signal) });
  const data = query.data;
  const displayProfile = data?.profile || profile;
  const kpis = { ...(profileResponse?.stats ?? {}), ...(data?.kpis ?? {}) };
  const currentYear = data?.heatmap?.[0]?.date ? Number(data.heatmap[0].date.slice(0, 4)) : new Date().getFullYear();

  if (query.isLoading) return <LoadingState />;
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />;

  return (
    <div className="page overview-page">
      <ProfileBanner
        profile={displayProfile}
        subtitle="本机资料 / 个人档案"
        tags={["联系人", "本地数据", "活跃用户"]}
        metrics={[
          { label: "消息总数", value: formatNumber(firstValue(kpis, ["messages", "message_count", "total_messages"])), suffix: "条消息", icon: <MessagesSquare /> },
          { label: "活跃天数", value: formatNumber(firstValue(kpis, ["active_days", "days"])), suffix: "天", icon: <CalendarDays /> },
          { label: "联系人数量", value: formatNumber(firstValue(kpis, ["contacts", "contact_count"])), suffix: "位联系人", icon: <ContactRound /> },
          { label: "最近活跃", value: formatDateTime(firstValue(kpis, ["last_active", "latest_message_time"])), note: data?.snapshot_time ? `快照 ${formatDateTime(data.snapshot_time)}` : undefined, icon: <Clock3 /> },
        ]}
      />

      <div className="grid overview-top-grid">
        <Card title={<><span>最近活跃趋势</span><span className="info-dot">i</span></>} action={<span className="select-chip">{range === "all" ? "全部时间" : `近 ${days} 天`}</span>}>
          <TrendChart data={data?.trend} height={196} />
        </Card>
        <Card title="常聊联系人" action={<SectionLink onClick={() => navigate("/contacts")}>查看全部</SectionLink>}>
          {data?.top_contacts?.length ? (
            <div className="rank-list">
              {data.top_contacts.slice(0, 5).map((contact, index) => (
                <button className="rank-row" type="button" key={contact.username} onClick={() => navigate(`/conversations?session=${encodeURIComponent(contact.username)}`)}>
                  <span className={`rank-index r${index + 1}`}>{index + 1}</span>
                  <Avatar src={contact.avatar_url} name={contact.display_name || contact.username} size={30} />
                  <span className="rank-name">{contact.display_name || contact.username}</span>
                  <span className="rank-summary">{safeText(contact.summary, "暂无可解码摘要")}</span>
                  <span className="rank-value">{formatNumber(contact.message_count)} 条</span>
                </button>
              ))}
            </div>
          ) : <EmptyState title="暂无联系人排行" />}
        </Card>
      </div>

      <div className="grid overview-bottom-grid">
        <Card title="消息类型占比">
          <DonutChart
            data={data?.message_types?.map((item) => ({ name: item.label || item.type, value: item.count }))}
            height={205}
            label="消息类型占比"
          />
        </Card>
        <Card title="最近消息预览" action={<SectionLink onClick={() => navigate("/conversations")}>查看全部</SectionLink>}>
          {data?.recent_messages?.length ? (
            <div className="recent-list">
              {data.recent_messages.slice(0, 5).map((message, index) => {
                const username = safeText(message.session_username, `message-${index}`);
                const name = safeText(message.session_name || message.sender_name, username);
                return (
                  <button className="recent-row" type="button" key={safeText(message.id, `${username}-${index}`)} onClick={() => navigate(`/conversations?session=${encodeURIComponent(username)}`)}>
                    <Avatar src={safeText(message.avatar_url, "")} name={name} size={28} />
                    <span className="recent-name">{name}</span>
                    <span className="recent-summary">{safeText(message.summary, message.decoded === false ? "内容解码失败" : "暂无文本摘要")}</span>
                    <time>{formatDateTime(message.create_time)}</time>
                  </button>
                );
              })}
            </div>
          ) : <EmptyState title="暂无最近消息" />}
        </Card>
      </div>

      <Card className="overview-heat-card" title={<>{currentYear} 年度活跃热力图 <span className="info-dot">i</span></>} action={<SectionLink onClick={() => navigate("/contributions")}>查看年度详情</SectionLink>}>
        <CalendarHeatmap data={data?.heatmap} year={currentYear} dense onSelect={(point) => navigate(`/conversations?date=${point.date}`)} />
        <div className="heat-footer"><span>按本地时区汇总真实消息记录</span><span className="heat-legend">活跃度：低 <i className="l0" /><i className="l1" /><i className="l2" /><i className="l3" /><i className="l4" /><i className="l5" /> 高</span></div>
      </Card>
    </div>
  );
}
