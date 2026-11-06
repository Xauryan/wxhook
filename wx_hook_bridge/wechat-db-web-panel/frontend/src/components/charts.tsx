import { CSSProperties, useMemo } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import "echarts-wordcloud";
import { GraphResponse, TrendPoint } from "../lib/api";
import { ChartEventParams, graphNodeIdFromEvent } from "../lib/chartEvents";
import { formatNumber } from "../lib/format";
import { EmptyState } from "./ui";

const GREEN = "#219653";
const GREEN_LIGHT = "#8ccfa5";
const GRID = "#edf0ee";
const TEXT = "#4c535d";

export function EChart({ option, height = 220, label, className = "", onEvents }: { option: EChartsOption; height?: number; label: string; className?: string; onEvents?: Record<string, (params: ChartEventParams) => void> }) {
  return <div className={`chart ${className}`} role="img" aria-label={label}><ReactECharts option={option} onEvents={onEvents} notMerge lazyUpdate style={{ height }} opts={{ renderer: "svg" }} /></div>;
}

export function TrendChart({ data, height = 220, mode = "bar", label = "消息趋势图", onSelectDate }: { data?: TrendPoint[]; height?: number; mode?: "bar" | "line"; label?: string; onSelectDate?: (date: string) => void }) {
  if (!data?.length) return <EmptyState title="暂无趋势数据" />;
  const option: EChartsOption = {
    animationDuration: 350,
    tooltip: { trigger: "axis", backgroundColor: "#fff", borderColor: "#e7e9e8", textStyle: { color: "#30343b", fontSize: 12 }, extraCssText: "box-shadow:0 8px 24px rgba(29,45,35,.12);border-radius:8px" },
    grid: { left: 38, right: 10, top: 18, bottom: 30 },
    xAxis: { type: "category", data: data.map((item) => item.date.slice(5)), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: "#868c92", fontSize: 10, interval: Math.max(0, Math.floor(data.length / 5) - 1) } },
    yAxis: { type: "value", splitNumber: 4, axisLabel: { color: "#868c92", fontSize: 10 }, splitLine: { lineStyle: { color: GRID, type: "dashed" } } },
    dataZoom: data.length > 30 ? [{ type: "inside", startValue: Math.max(0, data.length - 30), endValue: data.length - 1 }] : undefined,
    series: [{
      type: mode,
      data: data.map((item) => item.count),
      itemStyle: { color: GREEN, borderRadius: mode === "bar" ? [4, 4, 0, 0] : 0 },
      barMaxWidth: 8,
      symbolSize: 6,
      smooth: true,
      lineStyle: { width: 2, color: GREEN },
      areaStyle: mode === "line" ? { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "rgba(33,150,83,.22)" }, { offset: 1, color: "rgba(33,150,83,.01)" }] } } : undefined,
    }],
  };
  return <EChart option={option} height={height} label={`${label}，共 ${data.length} 个时间点`} onEvents={onSelectDate ? { click: (params) => { if (typeof params.dataIndex === "number") onSelectDate(data[params.dataIndex]?.date); } } : undefined} />;
}

export function DonutChart({ data, centerText, height = 200, label = "分类占比图" }: { data?: Array<{ name: string; value: number }>; centerText?: string; height?: number; label?: string }) {
  if (!data?.length) return <EmptyState title="暂无分类数据" />;
  const palette = ["#249956", "#82cf99", "#68b5ee", "#f4b83f", "#8e79e8", "#f27b65"];
  const option: EChartsOption = {
    color: palette,
    tooltip: { trigger: "item", formatter: "{b}<br/>{c} ({d}%)" },
    legend: { type: "scroll", orient: "vertical", right: 6, top: "middle", itemWidth: 9, itemHeight: 9, textStyle: { color: TEXT, fontSize: 11 }, formatter: (name: string) => {
      const item = data.find((entry) => entry.name === name);
      const total = data.reduce((sum, entry) => sum + entry.value, 0);
      const pct = item && total ? ((item.value / total) * 100).toFixed(1) : "0.0";
      return `${name}    ${pct}%  (${formatNumber(item?.value)})`;
    } },
    graphic: centerText ? [{ type: "text", left: "29%", top: "43%", style: { text: centerText, fill: "#20242a", fontSize: 20, fontWeight: 600 } }] : undefined,
    series: [{ type: "pie", radius: ["42%", "67%"], center: ["31%", "53%"], avoidLabelOverlap: true, label: { show: false }, emphasis: { scale: true, scaleSize: 4 }, data }],
  };
  return <EChart option={option} height={height} label={`${label}：${data.map((item) => `${item.name}${item.value}`).join("，")}`} />;
}

export function RelationshipGraph({ graph, height = 390, onNodeClick }: { graph?: GraphResponse; height?: number; onNodeClick?: (id: string) => void }) {
  if (!graph?.nodes?.length) return <EmptyState title="暂无关系网络数据" />;
  const max = Math.max(...graph.nodes.map((node) => node.value || 1));
  const option: EChartsOption = {
    tooltip: { formatter: "{b}<br/>互动 {c}" },
    series: [{
      type: "graph",
      layout: "force",
      roam: false,
      draggable: false,
      force: { repulsion: 260, edgeLength: [72, 130], gravity: 0.08 },
      label: { show: true, position: "bottom", color: "#30363d", fontSize: 11, distance: 5 },
      lineStyle: { color: "#6bbd8a", opacity: 0.58, curveness: 0, width: 1.5 },
      emphasis: { focus: "adjacency", lineStyle: { opacity: 1 } },
      data: graph.nodes.map((node, index) => ({
        ...node,
        symbol: node.avatar_url ? `image://${node.avatar_url}` : "circle",
        symbolSize: index === 0 ? 72 : 38 + Math.round(((node.value || 0) / max) * 12),
        itemStyle: { color: index === 0 ? "#f5efe3" : "#daf0e2", borderColor: index === 0 ? GREEN : "#a4d8b6", borderWidth: index === 0 ? 3 : 2 },
      })),
      links: graph.links.map((link) => ({ ...link, lineStyle: { width: 1 + Math.min(5, link.value / Math.max(1, max / 5)), color: GREEN, opacity: 0.25 + Math.min(0.65, link.value / max) } })),
    }],
  };
  return <EChart option={option} height={height} label={`联系人关系网络，共 ${graph.nodes.length} 个节点`} onEvents={onNodeClick ? { click: (params) => { const id = graphNodeIdFromEvent(params); if (id) onNodeClick(id); } } : undefined} />;
}

export function WordCloud({ data, height = 250 }: { data?: Array<{ word: string; count: number }>; height?: number }) {
  if (!data?.length) return <EmptyState title="暂无可解码文本关键词" />;
  const option: EChartsOption = {
    tooltip: { show: true },
    series: [{
      type: "wordCloud" as never,
      shape: "circle",
      left: "center",
      top: "center",
      width: "95%",
      height: "90%",
      sizeRange: [12, 44],
      rotationRange: [0, 0],
      gridSize: 10,
      data: data.map((entry) => {
        const palette = ["#1e9450", "#58a978", "#84b998", "#387e57"];
        const hash = [...entry.word].reduce((value, char) => ((value * 31) + char.charCodeAt(0)) >>> 0, 0);
        return { name: entry.word, value: entry.count, textStyle: { color: palette[hash % palette.length] } };
      }),
    }],
  };
  return <EChart option={option} height={height} label={`高频关键词词云，共 ${data.length} 个关键词`} />;
}

type HeatPoint = { date: string; count: number; [key: string]: unknown };

export function CalendarHeatmap({ data = [], year, selected, onSelect, dense = false }: { data?: HeatPoint[]; year: number; selected?: string; onSelect?: (point: HeatPoint) => void; dense?: boolean }) {
  const { cells, monthLabels, max } = useMemo(() => {
    const map = new Map(data.map((item) => [item.date, item]));
    const first = new Date(year, 0, 1);
    const last = new Date(year, 11, 31);
    const start = new Date(first);
    start.setDate(first.getDate() - first.getDay());
    const end = new Date(last);
    end.setDate(last.getDate() + (6 - last.getDay()));
    const list: Array<HeatPoint & { inYear: boolean; week: number; day: number }> = [];
    const labels: Array<{ label: string; week: number }> = [];
    let previousMonth = -1;
    let cursor = new Date(start);
    while (cursor <= end) {
      const week = Math.floor((cursor.getTime() - start.getTime()) / 604800000);
      const key = `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, "0")}-${String(cursor.getDate()).padStart(2, "0")}`;
      if (cursor.getFullYear() === year && cursor.getMonth() !== previousMonth) {
        labels.push({ label: `${cursor.getMonth() + 1}月`, week });
        previousMonth = cursor.getMonth();
      }
      list.push({ date: key, count: map.get(key)?.count ?? 0, ...(map.get(key) ?? {}), inYear: cursor.getFullYear() === year, week, day: cursor.getDay() });
      cursor = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + 1);
    }
    return { cells: list, monthLabels: labels, max: Math.max(1, ...data.map((item) => item.count || 0)) };
  }, [data, year]);

  function level(value: number): number {
    if (!value) return 0;
    const ratio = value / max;
    if (ratio < 0.08) return 1;
    if (ratio < 0.22) return 2;
    if (ratio < 0.45) return 3;
    if (ratio < 0.72) return 4;
    return 5;
  }

  return (
    <div className={`calendar-heatmap${dense ? " dense" : ""}`} style={{ "--weeks": Math.max(...cells.map((cell) => cell.week)) + 1 } as CSSProperties}>
      <div className="heat-months">{monthLabels.map((item) => <span key={item.label} style={{ gridColumn: item.week + 1 }}>{item.label}</span>)}</div>
      <div className="heat-weekdays" aria-hidden="true"><span>日</span><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span></div>
      <div className="heat-cells">
        {cells.map((cell) => (
          <button
            key={cell.date}
            type="button"
            className={`heat-cell l${level(cell.count)}${cell.date === selected ? " selected" : ""}${!cell.inYear ? " outside" : ""}`}
            style={{ gridColumn: cell.week + 1, gridRow: cell.day + 1 }}
            title={`${cell.date}：${formatNumber(cell.count)} 条`}
            aria-label={`${cell.date}，${formatNumber(cell.count)} 条消息`}
            disabled={!cell.inYear}
            onClick={() => onSelect?.(cell)}
          />
        ))}
      </div>
    </div>
  );
}
