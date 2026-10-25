# application-manifest

profile の変更が二段階の Argo CD 同期と Pod に届く流れは [4 repository 共通の学習ガイド](https://github.com/syuhei0519/platform-gitops/blob/205e9a652d9fa5b550c7cb813bf94a64fcfa7f62/docs/learning-guides/README.md) を参照してください。Abort と Git の前版復元の違い、timeout 後の照合、正常系・異常系の検証もまとめています。

## Existing kind deployment (PLAT-195/198)

For `kind-learning-20261018-69c763`, the ordinary deployment entry is a reviewed
Git commit/PR followed by Argo CD sync. Follow the [current deployment contract](https://github.com/syuhei0519/platform-gitops/blob/main/learning/existing-kind/DEPLOYMENT.md).
Manual workload apply, image mutation and rollout restart from historical learning
instructions are retired. Existing Argo Core is reused; the old platform-lab and
isolated Account trial instructions are not the current cluster's deployment route.
This entry-point change does not claim that the pending PLAT-177/199 acceptance has passed.


学習専用の手動・ローカルimage経路は [learning/README.md](learning/README.md)。
Account/Orderを先に、Payment/Notificationを個別に追加する独立設定で、実配備・受入は未検証です。

`charts/frontend`, `charts/backend`, `charts/postgresql` がaccount環境の実行状態を所有します。SecretとNamespaceは生成しません。

Chartのimage・release既定値は初回image publishまで未設定です。`environments/local/frontend.yaml` と `backend.yaml` は検証済みのsource SHA・digest・pipeline由来を保持する稼働値です。新規環境で未設定の値を使った場合はschema検証が意図的に失敗し、dummy imageをデプロイ可能と扱いません。

検証は `sh ci/validate.sh` で3 Chartのstrict lint、render、禁止resource、image不変性、migration/PVC保護を確認します。

Frontend、Backend、PostgreSQLの実行時Desired Stateを所有します。各コンポーネントは独立したHelm ChartとArgo CD Applicationとして実装します。

2026-10-01のPE-001読み取り確認では `kind-platform-lab` のfrontend/backend/PostgreSQL ApplicationはSynced / Healthyでした。これは将来の変更や新規環境の合格を保証しません。

SQL/migration実装はbackend-app、実行Jobとaccount固有DB/PVCは本repository、Argo Application/AppProject/Namespaceはplatform-gitopsが所有します。固定版台帳の正本はplatform-gitopsの `bootstrap/versions.lock.yaml` です。[PE-001基準記録](https://github.com/syuhei0519/platform-gitops/blob/main/docs/implementation/PE-001-baseline.md) を参照してください。

Order evidence verifier scope and B2 fixture contract: [M0](docs/order-trusted-manifest-m0.md).

## 構成と入口

アプリと専用 DB の配備定義を管理します。Helm の入力からリソースを作り、CI で由来と構成を確認します。

この階層のディレクトリと役割は次のとおりです。末端のファイルは親の説明から追えます。

| 場所 | 役割 |
| --- | --- |
| [.github/](.github/README.md) | GitHub の CI、Issue、PR の入口。実行条件は workflows に置きます。 |
| [charts/](charts/README.md) | Kubernetes のリソースを生成する Helm Chart。環境ごとの差は values で渡します。 |
| [ci/](ci/README.md) | 検査の実行手順と境界テスト。GitHub から呼ぶ処理は github にまとめます。 |
| [docs/](docs/README.md) | 設計上の約束、検証結果、運用・引き継ぎの文書。過去の記録と現在の手順を区別して読みます。 |
| [environments/](environments) | 環境ごとの Chart 上書き値。local がローカル環境向けの入力です。 |
| [learning/](learning/README.md) | テーマ別の学習・検証用定義と補助ツール。実行前に各 README の対象と前提を確認します。 |
| [security/](security/README.md) | 検査の合否条件と schema。アプリの機能設定とは分けて管理します。 |
| [tools/](tools/README.md) | CI から呼ぶ Go ツールと、共有ツールの利用手順。 |

### 本文にコメントを入れないファイル

JSON などの形式、埋め込み・checksum、既存 reader の文字コードの前提を保つため、役割はここで説明します。

| ファイル | 役割 |
| --- | --- |
| [renovate.json](renovate.json) | 依存と共有 CI の固定参照を更新する Bot の設定です。有効・無効の状態もここで指定します。 |

ファイルの説明範囲と除外理由は [棚卸し記録](docs/file-guide.md) を参照してください。
