// Backstage backend でリリース操作の許可を判定し、画面表示だけに権限制御を任せないようにします。
import { createBackendModule } from '@backstage/backend-plugin-api';
import { AuthorizeResult } from '@backstage/plugin-permission-common';
import { policyExtensionPoint } from '@backstage/plugin-permission-node/alpha';

export default createBackendModule({
  pluginId: 'permission',
  moduleId: 'account-read-only',
  register(env) {
    env.registerInit({
      deps: { policy: policyExtensionPoint },
      async init({ policy }) {
        policy.setPolicy({
          async handle(request) {
            // Catalog is a fixed Account mapping, never a provisioning portal.
            return { result: request.permission.attributes.action === 'read'
              ? AuthorizeResult.ALLOW : AuthorizeResult.DENY };
          },
        });
      },
    });
  },
});
