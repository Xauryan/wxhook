import { useQuery } from "@tanstack/react-query";
import { Database, LockKeyhole, RefreshCw, Server } from "lucide-react";
import { Card, ErrorState, LoadingState, Pill } from "../components/ui";
import { api, API_BASE } from "../lib/api";
import { formatDateTime, profileName, safeText } from "../lib/format";

export function SettingsPage() {
  const query = useQuery({ queryKey: ["profile"], queryFn: ({ signal }) => api.profile(signal) });
  if (query.isLoading) return <LoadingState />;
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />;
  const quality = query.data?.data_quality ?? {};
  const databaseCount = Number(quality.decrypted_database_count);
  const integrityCount = Number(quality.integrity_check_ok);
  const quickCount = Number(quality.quick_check_ok);
  const ratio = (value: number) => Number.isFinite(value) && Number.isFinite(databaseCount) ? `${value}/${databaseCount} 通过` : "暂无结果";
  const snapshotMode = quality.snapshot_mode === "base-only" ? "主库快照（未合并 WAL）" : safeText(quality.snapshot_mode);
  return <div className="page settings-page"><div className="settings-heading"><h1>本地设置</h1><p>查看当前数据源、只读状态和快照质量。</p></div><div className="settings-grid"><Card title={<><Database />数据源</>}><dl><div><dt>当前账号</dt><dd>{profileName(query.data?.profile)}</dd></div><div><dt>账号标识</dt><dd>{safeText(query.data?.profile?.username)}</dd></div><div><dt>API 地址</dt><dd><code>{API_BASE}</code></dd></div><div><dt>快照时间</dt><dd>{formatDateTime(query.data?.snapshot_time || quality.snapshot_time)}</dd></div></dl></Card><Card title={<><LockKeyhole />隐私与访问</>}><div className="privacy-list"><p><Pill tone="green">只读</Pill> 数据库由服务端以 immutable 只读连接打开。</p><p><Pill tone="green">本机</Pill> 页面与 API 仅监听 127.0.0.1。</p><p><Pill tone="green">无上传</Pill> 联系人、消息、头像和媒体不会发送到第三方。</p></div></Card><Card title={<><Server />数据质量</>}><dl><div><dt>完整性检查</dt><dd>{ratio(integrityCount)}</dd></div><div><dt>快速检查</dt><dd>{ratio(quickCount)}</dd></div><div><dt>快照模式</dt><dd>{snapshotMode}</dd></div><div><dt>指标依据</dt><dd>{safeText(query.data?.metric_basis)}</dd></div></dl></Card><Card title={<><RefreshCw />刷新</>}><p>重新读取当前本地快照和数据质量状态。</p><button className="green-button" type="button" onClick={() => query.refetch()} disabled={query.isFetching}>{query.isFetching ? "刷新中" : "立即刷新"}</button></Card></div></div>;
}
