// Backstage backend に必要な plugin と認証・権限処理を登録する起動入口です。
import { createBackend } from '@backstage/backend-defaults';
import accountRelease from './plugin';
import permissionPolicy from './permissionPolicy';

const backend = createBackend();
backend.add(import('@backstage/plugin-app-backend'));
backend.add(import('@backstage/plugin-auth-backend'));
// No auth provider is enabled by the committed config. An approved trial may
// explicitly enable the official development-only guest provider locally.
backend.add(import('@backstage/plugin-auth-backend-module-guest-provider'));
backend.add(import('@backstage/plugin-catalog-backend'));
backend.add(import('@backstage/plugin-catalog-backend-module-logs'));
backend.add(import('@backstage/plugin-permission-backend'));
backend.add(permissionPolicy);
backend.add(accountRelease);
backend.start();
