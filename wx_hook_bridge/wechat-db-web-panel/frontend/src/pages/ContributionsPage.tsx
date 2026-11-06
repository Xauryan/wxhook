import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, CalendarDays, Flame, Link2, MessageCircle, Quote, Sparkles } from "lucide-react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { CalendarHeatmap } from "../components/charts";
import { Avatar, Card, EmptyState, ErrorState, LoadingState, ProfileBanner, Pill } from "../components/ui";
import type { ShellContext } from "../components/AppShell";
import { api, ContributionDay } from "../lib/api";
import { formatDateTime, formatNumber, localDateKey, safeText } from "../lib/format";

export function ContributionsPage() {
  const navigate = useNavigate();
  const { profile, profileResponse } = useOutletContext<ShellContext>();
  const [year, setYear] = useState(new Date().getFullYear());
  const [selected, setSelected] = useState<string>();
  const [metric, setMetric] = useState<"count" | "quality" | "quoted">("count");
  const query = useQuery({ queryKey: ["contributions", year, selected], queryFn: ({ signal }) => api.contributions(year, selected, signal) });
  const data = query.data;
  useEffect(() => {
    if (!selected && data?.heatmap?.length) {
      const latest = [...data.heatmap].filter((item) => item.count > 0).sort((a, b) => b.date.localeCompare(a.date))[0];
      if (latest) setSelected(latest.date);
    }
  }, [data?.heatmap, selected]);

  if (query.isLoading) return <LoadingState label="正在计算年度贡献" />;
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />;

  const selectedPoint = data?.heatmap?.find((item) => item.date === selected);
  const selectedDay = data?.selected_day ?? {};
  const mappedHeatmap = data?.heatmap?.map((item) => ({ ...item, count: Number(item[metric] ?? 0) })) as ContributionDay[] | undefined;
  const profileStats = profileResponse?.stats ?? {};

  return (
    <div className="page contributions-page">
      <ProfileBanner
        compact
        profile={profile}
        subtitle="联系人 · 本地贡献记录"
        tags={[]}
        metrics={[
          { label: "消息总数", value: formatNumber(profileStats.messages), suffix: "条消息", icon: <MessageCircle /> },
          { label: "活跃天数", value: formatNumber(profileStats.active_days), suffix: "天", icon: <CalendarDays /> },
          { label: "联系人数量", value: formatNumber(profileStats.contacts), suffix: "位联系人", icon: <Link2 /> },
        ]}
        action={<button className="outline-button" type="button" onClick={() => navigate("/overview")}>查看详情 <ArrowRight size={15} /></button>}
      />

      <Card className="contribution-main-card">
        <div className="contribution-heading">
          <div><h2>{year} 年贡献画像 <span className="info-dot">i</span></h2><div className="metric-tabs"><button type="button" className={metric === "count" ? "active" : ""} onClick={() => setMetric("count")}>发言量</button><button type="button" className={metric === "quality" ? "active" : ""} onClick={() => setMetric("quality")}>有效贡献</button><button type="button" className={metric === "quoted" ? "active" : ""} onClick={() => setMetric("quoted")}>被引用</button></div></div>
          <div className="annual-kpis">
            <div><MessageCircle /><span>消息数<b>{formatNumber(data?.kpis?.messages)}</b></span></div>
            <div><CalendarDays /><span>活跃日<b>{formatNumber(data?.kpis?.active_days)}</b></span></div>
            <div><Flame /><span>最长连续<b>{formatNumber(data?.kpis?.longest_streak)} <small>天</small></b></span></div>
            <div><Quote /><span>被引用次数<b>{formatNumber(data?.kpis?.quoted)} <small>次</small></b></span></div>
          </div>
        </div>
        <CalendarHeatmap data={mappedHeatmap} year={year} selected={selected} onSelect={(point) => setSelected(point.date)} />
        <div className="contribution-controls"><span>深色代表当日{metric === "count" ? "发言量" : metric === "quality" ? "有效贡献" : "被引用"}更多</span><div><button type="button" aria-label="上一年" onClick={() => setYear((value) => value - 1)}><ArrowLeft size={14} /></button><span>{year} 年</span><button type="button" aria-label="下一年" onClick={() => setYear((value) => value + 1)} disabled={year >= new Date().getFullYear()}><ArrowRight size={14} /></button><button type="button" onClick={() => { setYear(new Date().getFullYear()); setSelected(localDateKey()); }}>今天</button></div></div>
      </Card>

      <section className="selected-day-banner">
        <div className="selected-date"><span><CalendarDays /></span><div><b>{selected || "请选择日期"}</b><small>{selected ? new Intl.DateTimeFormat("zh-CN", { weekday: "long" }).format(new Date(`${selected}T12:00:00`)) : ""}</small></div></div>
        <div className="active-period"><span>活跃时段</span><b>{safeText(selectedDay.active_period || selectedDay.time_range)}</b><small>{safeText(selectedDay.duration, "基于本地消息时间")}</small></div>
        {[
          ["发言", selectedPoint?.sent ?? selectedPoint?.count, "sent"],
          ["发言回合", selectedDay.turns, "turns"],
          ["有效贡献", selectedPoint?.quality ?? selectedDay.quality, "quality"],
          ["被引用", selectedPoint?.quoted ?? selectedDay.quoted, "quoted"],
          ["主动引用", selectedDay.outgoing_quotes, "outgoing"],
        ].map(([label, value, key]) => <div className="day-stat" key={String(key)}><span>{String(label)}</span><b>{formatNumber(value)}</b><small>当天真实统计</small></div>)}
        <button className="green-button" type="button" onClick={() => selected && navigate(`/conversations?date=${selected}`)}>查看当天消息 <ArrowRight size={15} /></button>
      </section>

      <div className="contribution-bottom-grid">
        <Card title={<><Sparkles size={16} fill="currentColor" /> 当天消息摘要</>}>
          {Array.isArray(selectedDay.messages) && selectedDay.messages.length ? <div className="day-message-list">{(selectedDay.messages as Array<Record<string, unknown>>).slice(0, 5).map((message, index) => <div key={safeText(message.id, String(index))}><Avatar src={safeText(message.avatar_url, "")} name={safeText(message.session_name, "会话")} size={30} /><b>{safeText(message.session_name, "会话")}</b><span>{safeText(message.summary, "无可解码摘要")}</span><time>{formatDateTime(message.create_time)}</time><Pill tone={message.quoted ? "green" : "neutral"}>{message.quoted ? "被引用" : "普通"}</Pill></div>)}</div> : <EmptyState title="所选日期暂无消息摘要" />}
        </Card>
        <Card title="当天洞察">
          {Array.isArray(selectedDay.insights) && selectedDay.insights.length ? <div className="insight-list">{(selectedDay.insights as Array<string | Record<string, unknown>>).map((insight, index) => <div key={index}><Sparkles size={19} /><p>{typeof insight === "string" ? insight : safeText(insight.text || insight.title)}</p></div>)}</div> : <EmptyState title="当天没有可可靠推导的洞察" />}
        </Card>
      </div>
    </div>
  );
}
