import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { CalendarDays, Clock3, ContactRound, MessagesSquare, UsersRound } from "lucide-react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { DonutChart, RelationshipGraph, TrendChart } from "../components/charts";
import { Avatar, Card, EmptyState, ErrorState, LoadingState, ProfileBanner, SectionLink } from "../components/ui";
import type { ShellContext } from "../components/AppShell";
import { api, Contact } from "../lib/api";
import { formatDateTime, formatDuration, formatNumber } from "../lib/format";

function buildGrowth(items: Contact[]) {
  const counts = new Map<string, number>();
  items.forEach((item) => {
    if (!item.first_interaction) return;
    const date = item.first_interaction.slice(0, 10);
    counts.set(date, (counts.get(date) ?? 0) + 1);
  });
  let total = 0;
  return [...counts.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([date, count]) => ({ date, count: total += count }));
}

function responseBuckets(items: Contact[]) {
  const buckets = [
    { name: "5 分钟内", min: 0, max: 300, value: 0 },
    { name: "5 分钟 - 1 小时", min: 300, max: 3600, value: 0 },
    { name: "1 - 24 小时", min: 3600, max: 86400, value: 0 },
    { name: "1 - 3 天", min: 86400, max: 259200, value: 0 },
    { name: "3 天以上", min: 259200, max: Number.POSITIVE_INFINITY, value: 0 },
  ];
  items.forEach((item) => {
    if (typeof item.response_median_seconds !== "number") return;
    const bucket = buckets.find((entry) => item.response_median_seconds! >= entry.min && item.response_median_seconds! < entry.max);
    if (bucket) bucket.value += 1;
  });
  return buckets;
}

function channelLabels(channels?: string[], isGroup?: boolean): string[] {
  const labels: Record<string, string> = {
    text: "文本", image: "图片", file: "文件", voice: "语音", video: "视频",
    link: "链接", quote: "引用", sticker: "表情", transfer: "转账", location: "位置",
  };
  const normalized = (channels ?? []).flatMap((channel) => channel.split(/[·,|\s]+/)).map((channel) => labels[channel.toLowerCase()]).filter((label): label is string => Boolean(label));
  const unique = [...new Set(normalized)].slice(0, 4);
  if (isGroup && !unique.includes("群聊")) unique.unshift("群聊");
  return unique.slice(0, 4);
}

export function ContactsPage() {
  const navigate = useNavigate();
  const { profile, profileResponse } = useOutletContext<ShellContext>();
  const contactsQuery = useQuery({ queryKey: ["contacts", 100], queryFn: ({ signal }) => api.contacts({ limit: 100, offset: 0 }, signal) });
  const graphQuery = useQuery({ queryKey: ["contacts-graph", 18], queryFn: ({ signal }) => api.graph(18, signal) });
  const items = contactsQuery.data?.items ?? [];
  const growth = useMemo(() => buildGrowth(items), [items]);
  const response = useMemo(() => responseBuckets(items), [items]);
  const groups = items.filter((item) => item.is_group).length;
  const bidirectional = useMemo(() => {
    const strong = items.filter((item) => (item.bidirectional_ratio ?? 0) >= 0.75).length;
    const medium = items.filter((item) => (item.bidirectional_ratio ?? 0) >= 0.45 && (item.bidirectional_ratio ?? 0) < 0.75).length;
    const low = Math.max(0, items.length - strong - medium);
    return [{ name: "强互动", value: strong }, { name: "中等互动", value: medium }, { name: "低互动", value: low }].filter((item) => item.value > 0);
  }, [items]);

  if (contactsQuery.isLoading || graphQuery.isLoading) return <LoadingState />;
  if (contactsQuery.isError) return <ErrorState error={contactsQuery.error} retry={() => contactsQuery.refetch()} />;

  const stats = profileResponse?.stats ?? {};
  const responseValues = items.map((item) => item.response_median_seconds).filter((value): value is number => typeof value === "number").sort((a, b) => a - b);
  const medianResponse = responseValues.length ? responseValues[Math.floor(responseValues.length / 2)] : undefined;

  return (
    <div className="page contacts-page">
      <ProfileBanner
        profile={profile}
        subtitle="联系人关系洞察"
        tags={["联系人", "真实互动", "社交网络"]}
        metrics={[
          { label: "联系人总数", value: formatNumber(stats.contacts), suffix: "位联系人", note: `${formatNumber(contactsQuery.data?.total)} 位有消息互动`, icon: <ContactRound /> },
          { label: "高频联系人", value: formatNumber(items.filter((item) => (item.relationship_strength ?? 0) >= 70).length), suffix: "位", icon: <MessagesSquare /> },
          { label: "群聊数量", value: formatNumber(groups), suffix: "个群聊", icon: <UsersRound /> },
          { label: "平均响应时长", value: formatDuration(medianResponse), note: "基于可计算的一对一回复", icon: <Clock3 /> },
        ]}
      />

      <div className="contacts-dashboard">
        <Card className="network-card" title={<>联系人关系网络图 <span className="info-dot">i</span></>} action={<span className="network-legend">互动强度 <i /><i /><i /><i /></span>}>
          {graphQuery.isError ? <ErrorState error={graphQuery.error} retry={() => graphQuery.refetch()} /> : <RelationshipGraph graph={graphQuery.data} height={410} onNodeClick={(id) => navigate(`/conversations?session=${encodeURIComponent(id)}`)} />}
        </Card>
        <Card className="top-contacts-card" title="高频联系人 TOP 6">
          {items.length ? (
            <div className="contact-top-table table-like">
              <div className="table-head"><span>联系人</span><span>消息数</span><span>占比</span></div>
              {items.slice(0, 6).map((item, index) => {
                const total = items.reduce((sum, contact) => sum + (contact.message_count ?? 0), 0);
                return <button key={item.username} type="button" onClick={() => navigate(`/conversations?session=${encodeURIComponent(item.username)}`)}><span className="rank-index">{index + 1}</span><Avatar src={item.avatar_url} name={item.display_name || item.username} size={28} /><span>{item.display_name || item.username}</span><b>{formatNumber(item.message_count)}</b><em>{total ? `${(((item.message_count ?? 0) / total) * 100).toFixed(1)}%` : "—"}</em></button>;
              })}
            </div>
          ) : <EmptyState />}
        </Card>
        <Card className="interaction-card" title="双向互动强度分布" action={<SectionLink onClick={() => document.querySelector(".key-contacts-card")?.scrollIntoView({ behavior: "smooth" })}>查看全部</SectionLink>}>
          <DonutChart data={bidirectional} centerText={formatNumber(contactsQuery.data?.total)} height={210} label="双向互动强度分布" />
        </Card>
        <Card className="growth-card" title="首次互动联系人增长" action={<span className="select-chip">全部时间</span>}>
          <TrendChart data={growth} mode="line" height={150} label="首次互动联系人累计增长" />
        </Card>
        <Card className="response-card" title={<>响应速度分布 <span className="info-dot">i</span></>}>
          <div className="progress-list">
            {response.map((bucket) => {
              const max = Math.max(1, ...response.map((item) => item.value));
              return <div key={bucket.name}><span>{bucket.name}</span><i><b style={{ width: `${(bucket.value / max) * 100}%` }} /></i><em>{formatNumber(bucket.value)}</em></div>;
            })}
          </div>
        </Card>
      </div>

      <Card className="key-contacts-card" title="关键联系人" action={<SectionLink>查看全部</SectionLink>}>
        {items.length ? (
          <div className="contacts-table">
            <div className="contacts-table-head"><span>联系人</span><span>关系强度</span><span>消息数</span><span>互动频率</span><span>最近活动</span><span>主要互动渠道</span></div>
            {items.slice(0, 5).map((item, index) => (
              <button type="button" key={item.username} onClick={() => navigate(`/conversations?session=${encodeURIComponent(item.username)}`)}>
                <span className="contact-cell"><em>{index + 1}</em><Avatar src={item.avatar_url} name={item.display_name || item.username} size={27} />{item.display_name || item.username}</span>
                <span className="strength-cell"><b>{Math.round(item.relationship_strength ?? 0)}</b><i><u style={{ width: `${Math.min(100, item.relationship_strength ?? 0)}%` }} /></i></span>
                <span>{formatNumber(item.message_count)}</span>
                <span>{item.active_days ? `${formatNumber(item.active_days)} 活跃日` : "—"}</span>
                <span>{formatDateTime(item.last_active)}</span>
                <span className="channel-cell">{(channelLabels(item.channels, item.is_group).length ? channelLabels(item.channels, item.is_group) : [item.is_group ? "群聊" : "文本"]).map((label) => <i key={label}>{label}</i>)}</span>
              </button>
            ))}
          </div>
        ) : <EmptyState title="暂无关键联系人" />}
      </Card>
    </div>
  );
}
