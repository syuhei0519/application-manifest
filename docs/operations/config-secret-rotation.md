# ConfigMap再配置とDB認証情報更新の運用・実受入

frontend/backend Deploymentのchecksum/configは各chartのConfigMap template全体から計算する。Secret値はchart入力・checksum・Gitへ含めない。設定だけで新ReplicaSet/Podとなる。frontend imageのNginx設定は単独起動用の既定、chartのfrontend-configが/etc/nginx/conf.dを所有する。React bundleへSecretを入れず、既定API /apiと環境API pathを区別する。Nginxのvolume内容更新だけでreloadすると仮定しない。

## PE-005実受入（2026-10-02）

MR28の設定のみ変更でimage不変、新checksum/RS/Pod、新API /api-pe005 JSON CRUDとbackend debug起動ログを実測。MR32はService.portだけ80→8088、両PodTemplate/checksum/revision/UID不変、新Service port経由CRUD成功。MR33は元environment YAMLへGit復元し既定/api/INFO/port80、新Pod、元checksum/image、CRUD成功。静的checksumテストは全7設定キー、Service-only不変、Secret参照がConfigMap checksumへ混入しないことを確認する。実受入と証跡は[platform](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/blob/main/docs/implementation/PE005-acceptance.md)。

## DB rotationの順序

対象context/namespace/Secret/role、復元可能な元credentialのvault保管元を先に確定する。今回の利用者は隔離試験を包括承認済み。通常運用では担当者が影響と復帰手順を確認する。account-dbキーはadmin-password/app-username/app-password。アプリのSecret環境変数は既存connection poolに残るので、Secret更新だけを成功判定にしない。POSTGRES_PASSWORD_FILEは既存PVCのrole passwordを変更しない。

1. 元管理者・アプリ資格情報で**新規Pod IP TCP接続**の認証を確認し、Secret resourceVersion、DB/Backend Pod UID、PVC UID、nonceを記録する。unix/localhostはHBA trustなのでPW試験には使わない。
2. DBのadmin/app role passwordを一貫して更新し、SecretをresourceVersion CASで更新する。SQL、接続URL、PW、Secret data、pgpassをログ/履歴/証跡に含めない。今回の旧credentialはprivate process memoryだけ、新PWはランダム値、SQLとSecretはstdinで渡した。
3. **DB Podを先に再起動する。** PVCを消さず、Secret volumeから起動時/tmp/pgpassを再生成する。readinessはDownward API POD_IPへapp roleでSELECT 1するため実認証を確認する。元PW2種で新規接続拒否、新PW2種で新規接続成功、DB Readyを記録する。loopbackのpg_isreadyだけで認証成功としない。
4. Git restartNonce変更MRをCI/固定main検証/直前鮮度/tree照合後に統合し、backend Application全体を同期する。DB readinessが回復してからmigrationを動かし、PG Service endpoints欠落の循環待ちを避ける。新backend Pod UIDとnonce、image/ConfigMap checksum不変、frontend経由CRUDを確認する。
5. 復帰はDB roleとSecretを**同じ元credentialへ戻し**、DB Podを再起動してpgpassと認証probeを更新。元PW2種の新規接続成功、試験PW2種の拒否を確認する。その後Gitで元nonceへ戻してbackend新Pod/CRUDを確認する。Secretだけのrevertや既存pool成功を復帰完了としない。

## PE-006B実受入（2026-10-02）

MR31のPod IP認証readinessをmainへ統合し、両kindでDB Pod Ready。隔離kind-core-platform-at0102だけで両roleとSecretを更新、PG Podを置換して旧admin/app拒否・新admin/app成功を各新規接続で実測。MR34のnonceでbackend新PodとCRUD成功。role/Secretを元に戻してPG Pod再起動、元admin/app成功・試験admin/app拒否を実測。MR35で元nonceへ戻しbackend新Pod・CRUD成功。全段階で同PVC UID、imageとConfigMap checksum不変。SecretのresourceVersionとPod UIDだけを証跡へ記録した。private credential processは認証情報の復帰後に終了した。

SourceLabのDB role/Secret値/dataを変更していない。account-dbのUID/resourceVersion不変、共通Git nonceのbackend再配置は元Secretで成立。Secret名/キー/発行主体/最小権限と再注入（PE-006A）は[正本契約](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/blob/main/docs/secret-contract.md)。[実証跡](../evidence/PE006-rotation-runtime.json)はPW/base64/SQL/認証headerを含まない。DB/PVC/dataの削除やPhase 2のPackage/scan/SBOM導入は実施していない。