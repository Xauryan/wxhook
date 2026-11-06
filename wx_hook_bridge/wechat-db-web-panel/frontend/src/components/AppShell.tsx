import { FormEvent, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BellRing,
  CalendarDays,
  ChevronDown,
  CircleUserRound,
  ContactRound,
  Database,
  Home,
  Image,
  Layers3,
  List,
  MessageCircle,
  Newspaper,
  RefreshCw,
  Search,
  Settings,
  Truck,
  Sparkles,
} from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import { formatDateTime, profileName } from "../lib/format";
import { useRealtimeSubscription } from "../hooks/useRealtimeSubscription";
import { Avatar } from "./ui";

const navigation = [
  { to: "/overview", label: "概览", icon: Home },
  { to: "/contacts", label: "联系人", icon: ContactRound },
  { to: "/intention-notifications", label: "意向单通知", icon: BellRing },
  { to: "/delivery-management", label: "配送管理", icon: Truck },
  { to: "/conversations", label: "会话", icon: MessageCircle },
  { to: "/chat-list", label: "会话列表", icon: List },
  { to: "/contributions", label: "贡献", icon: Layers3 },
  { to: "/media", label: "媒体", icon: Image },
  { to: "/sns", label: "朋友圈", icon: Sparkles },
  { to: "/mp", label: "公众号", icon: Newspaper },
  { to: "/search", label: "搜索", icon: Search },
  { to: "/settings", label: "设置", icon: Settings },
];

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const profileQuery = useQuery({ queryKey: ["profile"], queryFn: ({ signal }) => api.profile(signal) });
  const realtime = useRealtimeSubscription();
  const isChatList = location.pathname.startsWith("/chat-list");

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    const value = query.trim();
    navigate(value ? `/search?q=${encodeURIComponent(value)}` : "/search");
  }

  const dataQuality = profileQuery.data?.data_quality;
  const snapshot = realtime.snapshotTime || profileQuery.data?.snapshot_time || dataQuality?.snapshot_time;
  const databaseCount = Number(dataQuality?.decrypted_database_count);
  const integrityCount = Number(dataQuality?.integrity_check_ok);
  const integrityLabel = Number.isFinite(databaseCount) && Number.isFinite(integrityCount)
    ? `${integrityCount}/${databaseCount} 通过`
    : profileQuery.isLoading ? "读取中" : "暂无校验结果";
  const snapshotMode = dataQuality?.snapshot_mode === "base-only" ? "本机主库快照" : "本机数据库";
  const realtimeLabel = realtime.connectionState === "connected"
    ? "已连接"
    : realtime.connectionState === "connecting"
      ? "连接中"
      : realtime.connectionState === "unsupported"
        ? "不支持"
        : "已断线";
  const snapshotLabel = snapshot ? "快照已载入" : "快照未知";

  return (
    <div className={`app-frame${isChatList ? " chat-list-frame" : ""}`}>
      <header className="topbar">
        <button className="brand" type="button" onClick={() => navigate("/overview")} aria-label="返回概览">
          <span className="brand-mark"><MessageCircle size={22} fill="currentColor" /></span>
          <span>微信本地数据库可视化</span>
        </button>
        <div className="topbar-actions">
          <form className="global-search" onSubmit={submitSearch} role="search">
            <input
              aria-label="搜索联系人、会话或内容"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索联系人 / 会话 / 内容"
            />
            <button type="submit" aria-label="开始搜索"><Search size={19} /></button>
          </form>
          <label className="top-filter">
            <CalendarDays size={17} />
            <select aria-label="全局时间范围" value={searchParams.get("range") ?? "all"} onChange={(event) => { const next = new URLSearchParams(searchParams); next.set("range", event.target.value); setSearchParams(next); }}>
              <option value="30">近 30 天</option>
              <option value="90">近 90 天</option>
              <option value="all">全部时间</option>
            </select>
            <ChevronDown size={15} />
          </label>
          <button className="account-button" type="button" onClick={() => navigate("/settings")} aria-label="账号与设置">
            <Avatar profile={profileQuery.data?.profile} size={36} />
            <ChevronDown size={14} />
          </button>
        </div>
      </header>

      <aside className="sidebar">
        <nav aria-label="主导航">
          {navigation.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
              <Icon size={21} strokeWidth={1.8} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="source-card" aria-label="数据库状态">
          <div className="source-label">数据范围</div>
          <div className="source-value"><Database size={13} fill="currentColor" /> {snapshotMode}</div>
          <div className="source-label source-label-spaced">实时订阅</div>
          <div className="realtime-badges" aria-live="polite">
            <span className={`realtime-badge ${realtime.connectionState}`} data-realtime-state={realtime.connectionState}>
              <i /> {realtimeLabel}
            </span>
            <span className={`realtime-badge snapshot ${snapshot ? "ready" : "pending"}`} title={snapshot ? `快照时间 ${formatDateTime(snapshot)}` : "尚未取得快照时间"}>
              {snapshotLabel}
            </span>
          </div>
          <div className="source-divider" />
          <div className="source-label">完整性状态</div>
          <div className="source-value"><span className={`status-dot ${profileQuery.isError || integrityCount < databaseCount ? "bad" : ""}`} /> {integrityLabel}</div>
          <div className="source-label source-label-spaced">统计更新</div>
          <div className="source-value"><RefreshCw size={13} /> {snapshot ? formatDateTime(snapshot) : profileQuery.isLoading ? "读取中" : "暂无快照"}</div>
        </div>
      </aside>

      <main className={`main-content${isChatList ? " chat-list-main" : ""}`}>
        <Outlet context={{ profile: profileQuery.data?.profile, profileResponse: profileQuery.data, profileName: profileName(profileQuery.data?.profile) }} />
      </main>
    </div>
  );
}

export type ShellContext = {
  profile?: import("../lib/api").Profile;
  profileResponse?: import("../lib/api").ProfileResponse;
  profileName: string;
};
