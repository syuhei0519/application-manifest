// Backstage の画面経路と認証を組み立て、カタログやリリース画面へつなぎます。
import { Navigate, Route } from 'react-router-dom';
import { createApp } from '@backstage/app-defaults';
import { AppRouter, FlatRoutes } from '@backstage/core-app-api';
import { AlertDisplay, OAuthRequestDialog, SignInPage } from '@backstage/core-components';
import { CatalogIndexPage, CatalogEntityPage, EntityLayout, EntityAboutCard } from '@backstage/plugin-catalog';
import { ReleasePage } from './ReleasePage';

const app = createApp({
  components: { SignInPage: props => <SignInPage {...props} providers={['guest']} /> },
});

export default app.createRoot(<>
  <AlertDisplay /><OAuthRequestDialog />
  <AppRouter><FlatRoutes>
    <Route path="/" element={<Navigate to="/catalog/default/component/account/release" replace />} />
    <Route path="/catalog" element={<CatalogIndexPage />} />
    <Route path="/catalog/:namespace/:kind/:name" element={<CatalogEntityPage />}>
      <EntityLayout>
        <EntityLayout.Route path="/" title="概要"><EntityAboutCard /></EntityLayout.Route>
        <EntityLayout.Route path="/release" title="リリース"><ReleasePage /></EntityLayout.Route>
      </EntityLayout>
    </Route>
  </FlatRoutes></AppRouter>
</>);
