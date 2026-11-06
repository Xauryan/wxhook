export type ChartEventParams = { dataIndex?: number; value?: unknown; dataType?: string; data?: unknown };

export function graphNodeIdFromEvent(params: ChartEventParams): string | undefined {
  if (params.dataType !== "node" || !params.data || typeof params.data !== "object") return undefined;
  const id = (params.data as { id?: unknown }).id;
  return typeof id === "string" && id ? id : undefined;
}
