export type Pending = { id: string; kind: string; state: string };
export const pendingKey = 'account-release-request-index-v1';
export function readPending(storage: Pick<Storage,'getItem'>): Pending[] {
  const data = JSON.parse(storage.getItem(pendingKey) ?? '[]');
  if (!Array.isArray(data) || data.some(x => !x || !/^[A-Za-z0-9_-]{8,80}$/.test(x.id) ||
    typeof x.kind !== 'string' || typeof x.state !== 'string')) throw new Error('saved-request-index-invalid');
  return data;
}
export function uncertain(rows: Pending[]) {
  return rows.some(x => ['accepted','dispatching','outcome-unknown'].includes(x.state));
}
export function beginPending(storage: Pick<Storage,'getItem'|'setItem'>, id: string, kind: string) {
  const rows=readPending(storage);
  if (uncertain(rows)) throw new Error('reconcile-existing-request-first');
  if (rows.some(x=>x.id===id)) throw new Error('request-id-reused');
  storage.setItem(pendingKey,JSON.stringify([...rows,{id,kind,state:'outcome-unknown'}]));
}
export function settlePending(storage: Pick<Storage,'getItem'|'setItem'>, result: Pending) {
  const rows=readPending(storage);
  if (!rows.some(x=>x.id===result.id)) throw new Error('saved-request-not-found');
  storage.setItem(pendingKey,JSON.stringify(rows.map(x=>x.id===result.id?{...x,state:result.state}:x)));
}
