/** Missing or invalid controller windows must stay visible as missing data. */
export function observationTime(seconds: unknown): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '未取得';
  const value = new Date(seconds * 1000);
  return Number.isNaN(value.getTime()) ? '未取得' : value.toISOString();
}

function isRate(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
}

export function rateText(value: unknown): string {
  return isRate(value) ? (value * 100).toFixed(2) + '%' : '欠測';
}

export function errorRateText(availability: unknown): string {
  return isRate(availability) ? rateText(1 - availability) : '欠測';
}
