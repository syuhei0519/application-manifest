# application-manifest

`charts/frontend`, `charts/backend`, `charts/postgresql` がaccount環境の実行状態を所有します。SecretとNamespaceは生成しません。

Chartのimage・release既定値は初回image publishまで未設定です。`environments/local/frontend.yaml` と `backend.yaml` は検証済みのsource SHA・digest・pipeline由来を保持する稼働値です。新規環境で未設定の値を使った場合はschema検証が意図的に失敗し、dummy imageをデプロイ可能と扱いません。

検証は `sh ci/validate.sh` で3 Chartのstrict lint、render、禁止resource、image不変性、migration/PVC保護を確認します。

Frontend、Backend、PostgreSQLの実行時Desired Stateを所有します。各コンポーネントは独立したHelm ChartとArgo CD Applicationとして実装します。

2026-10-01のPE-001読み取り確認では `kind-platform-lab` のfrontend/backend/PostgreSQL ApplicationはSynced / Healthyでした。これは将来の変更や新規環境の合格を保証しません。

SQL/migration実装はbackend-app、実行Jobとaccount固有DB/PVCは本repository、Argo Application/AppProject/Namespaceはplatform-gitopsが所有します。固定版台帳の正本はplatform-gitopsの `bootstrap/versions.lock.yaml` です。[PE-001基準記録](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/blob/main/docs/implementation/PE-001-baseline.md) と [PE-001 Issue](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/1) を参照してください。
