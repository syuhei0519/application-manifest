/** Missing or invalid controller windows must stay visible as missing data. */
export function observationTime(seconds: unknown): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '未取得';
  const value = new Date(seconds * 1000);
  return Number.isNaN(value.getTime()) ? '未取得' : value.toISOString();
}
