import { createBackendPlugin, coreServices } from '@backstage/backend-plugin-api';
import { NotAllowedError } from '@backstage/errors';
import express from 'express';
import { execFile } from 'node:child_process';
import path from 'node:path';

export default createBackendPlugin({
  pluginId: 'account-release',
  register(env) {
    env.registerInit({
      deps: { config: coreServices.rootConfig, httpAuth: coreServices.httpAuth,
        httpRouter: coreServices.httpRouter },
      async init({ config, httpAuth, httpRouter }) {
        const router = express.Router();
        router.use(express.json({ limit: '16kb', strict: true }));
        const actors = config.getStringArray('accountRelease.allowedActors');
        const writable = config.getBoolean('accountRelease.writesEnabled');
        const mode = config.getString('accountRelease.mode');
        const invoke = (operation: string, actor: string, payload: unknown) => new Promise<any>((resolve, reject) => {
          if (mode === 'disconnected') {
            resolve({ ok: false, code: 'connection-not-configured' });
            return;
          }
          const directory = config.getString('accountRelease.coreDirectory');
          const settings = config.getString('accountRelease.adapterConfig');
          const python = config.getString('accountRelease.python');
          if (![directory, settings, python].every(path.isAbsolute)) {
            reject(new Error('invalid-server-configuration'));
            return;
          }
          // No command, path, repo, ref or identity comes from the browser.
          const child = execFile(python, [path.join(directory, 'control_bridge.py'), '--config', settings],
            { timeout: 20000, maxBuffer: 2 * 1024 * 1024, windowsHide: true }, (error, stdout) => {
              if (error) { reject(new Error('operation-outcome-unknown')); return; }
              try { resolve(JSON.parse(stdout)); } catch { reject(new Error('invalid-adapter-response')); }
            });
          child.stdin?.end(JSON.stringify({ operation, actor, payload }));
        });
        const route = (operation: string, write: boolean) => async (req: express.Request, res: express.Response) => {
          try {
            const credentials = await httpAuth.credentials(req, { allow: ['user'] });
            const actor = credentials.principal.userEntityRef;
            if (!actors.includes(actor) || (write && !writable)) throw new NotAllowedError();
            const result = await invoke(operation, actor, write ? req.body : {});
            res.status(result.ok ? 200 : result.code === 'connection-not-configured' ? 503 : 409).json(result);
          } catch (error) {
            if (error instanceof NotAllowedError) { res.status(403).json({ ok: false, code: 'operation-not-authorized' }); return; }
            // Never forward credentials, subprocess stderr, SDK errors or raw request data.
            res.status(503).json({ ok: false, code: 'operation-outcome-unknown', reconcileRequired: true });
          }
        };
        router.get('/state', route('state', false));
        router.get('/history', route('history', false));
        router.post('/preview', route('preview', true));
        router.post('/request', route('request', true));
        router.post('/action', route('action', true));
        router.post('/reconcile', route('reconcile', true));
        router.post('/advance', route('advance', true));
        router.post('/apply_reviewed', route('apply_reviewed', true));
        httpRouter.use(router);
      },
    });
  },
});
