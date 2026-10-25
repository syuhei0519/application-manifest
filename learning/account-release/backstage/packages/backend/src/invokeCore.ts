// Python の操作 bridge を期限付きで呼び出し、ブラウザー切断だけで操作の結果を見失わないようにします。
import { execFile } from 'node:child_process';
import path from 'node:path';

// A phase advance reads runtime, verifies the prior merge, then creates the next
// proposal. Its bounded subprocess calls legitimately exceed the old 20s total.
// HTTP client disconnects must not cancel an already accepted mutation.
export function invokeCore(python: string, directory: string, settings: string,
  envelope: unknown, launch: typeof execFile = execFile) {
  return new Promise<any>((resolve, reject) => {
    if (![directory, settings, python].every(path.isAbsolute)) {
      reject(new Error('invalid-server-configuration')); return;
    }
    const child = launch(python, [path.join(directory, 'control_bridge.py'), '--config', settings],
      { timeout: 120000, maxBuffer: 2 * 1024 * 1024, windowsHide: true, encoding: 'utf8' },
      (error, stdout) => {
        if (error) { reject(new Error('operation-outcome-unknown')); return; }
        try { resolve(JSON.parse(String(stdout))); }
        catch { reject(new Error('invalid-adapter-response')); }
      });
    child.stdin?.end(JSON.stringify(envelope));
  });
}
