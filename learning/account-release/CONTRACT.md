# Account Release MVP 契約（PLAT-169）

対象はAccount一つ、cleanな専用kind一つ。実装正本はapplication-manifest。
この文書と`contract.py`は後続UI/APIの共有仕様・table testであり、Backstageや書込みAPIの実装完了ではない。
PLAT-171〜177、DB/data rollback、他service、外部公開、GitLab/K3s/共有環境は対象外。

## 固定対象・依存

| 対象 | 値・出所 |
| --- | --- |
| kind名 / context | plat170-account / kind-plat170-account（今回専用の新規案。既存なら開始拒否） |
| app namespace | learning-account-release |
| control namespace / Application / Rollout | learning-account-control / account-release / account-release |
| Istio経路 | account-release Service → account-release VSのprimary route → DR stable/canary subset |
| metrics selector | job=account-backend, account_namespace=learning-account-release, learning_service=account, rollout_revision=実Pod hash |
| code base | backend-app f0425a9c0a70c200fbeb5a549bc8b631bf5a1906 |
| manifest base | 226c9bf68833d3153a78c33f051175918ad7918d |
| platform base | 867cb81f071d06f7521b93aa110ae001200142a4 |
| image / previous image | 承認済みcatalogの異なるOCI manifest digestを必須入力。source commit、config digest、linux/amd64、build/scan出所を併記 |

実image catalogは未確定。2026-10-22にPCで確認できた旧Accountはsource
`e030275abbf52ecd27b7152ce597b3d89be3f830`、config digest
`sha256:864b695a3b0bb730e8a6fa8514fdde3b245204e8b23d5ece37fb21f8d8642684`のみ。
RepoDigestは空だった。config IDやtagをmanifest digestに見せ替えない。fixtureのexample.invalid imageは実行不可。
catalogを検証し新旧digestを固定するまでruntime開始を拒否する。前版は開始時の実stable imageと一致させる。

PLAT-80/82/83/93/94/95/96/99/101は既存Canary/復帰/監視の部品、
PLAT-85/102/166は専用demoのIstio、PLAT-86/165はAccount metricsの出所。
2026-10-22のJiraでは完了だが、今回のAccount mesh統合や新環境の受入証拠ではない。
PLAT-105/117/118/119は未着手・停止継続。後続閲覧UIで再利用し、今回勝手に再開・重複実装しない。

## 入力とAPI境界

画面入力は許可catalogのdigest、Canary/Rolling、Canaryだけmanual/auto。
Rollingのmodeはnull。promote/pause/resume UIを表示せず、APIでも拒否する。
対象context/namespace/URL、image文字列、metrics query、Git pathを画面で自由入力させない。

| API案 | 契約 |
| --- | --- |
| GET /account/releases/current | releaseId、version、Git commit、desired/observed digest・hash・weight、state、verdict、reason、allowedActions、観測UTC窓を返す |
| POST /account/releases | image/previousImage/strategy/mode/expectedVersion/idempotencyKey。提案のみ作成し202/proposed。承認前Git変更なし |
| POST /account/releases/{id}/approve | 人のGit変更承認とproposal hash/expectedVersionを確認。承認済み変更要求を作成する契約。merge承認を含まない |
| POST /account/releases/{id}/promote,pause,resume,abort | scoped認可、expectedVersion、runtime hash、Git revision、operation空、排他lockを照合してから操作 |
| POST /account/releases/{id}/restore | 旧digestへのGit変更を別途承認し、再同期後に検証。runtime abortだけでは永続復元完了にしない |

同一idempotencyKey+同一payloadは保存済み結果を返す。異なるpayloadは409。
同時開始はtarget単位の永続lock+CASで一件だけ受理する。stale画面/version/hash/revision不一致は409で再取得。
方式・manual/auto変更はrelease終了まで禁止。UI切断はcontrollerを止めず、再接続はGETで実状態を再取得。
403は未認可、409は競合、422は不正入力、503は観測不能。timeoutの書込み結果は先にread-backし、再送しない。
reference modelはこの永続lock・認証・Git/cluster adapterを実装しない。それらは後続課題のACで実証する。
adapterはpromote/resume/rolling_readyの直前にも現在時刻で観測鮮度・期待revisionを再検証する。
stateに保存したpassedだけを期限なしの操作許可として使わない（純粋modelは時計やI/Oを持たない）。

## 状態と操作

| 現状態 | 許可event | 次状態 / 条件 |
| --- | --- | --- |
| idle/succeeded/restored/failed | start | proposed、catalog・前版・CAS・driftなし |
| proposed | approve_git | syncing、人のGit承認必須 |
| syncing | synced / abort | Canary10又はrolling / aborting |
| canary10/50/100 manual | metrics / promote / pause / resume / abort | metrics passed後promoteで50/100/succeeded。pauseは段階を保持 |
| canary10/50/100 auto | metrics / pause / resume / abort | passedかつpauseなしで50/100/succeeded |
| rolling | metrics / rolling_ready / abort | Readyかつpassedでsucceeded。Canary操作拒否 |
| 観測中 | metrics failed | aborting。閾値違反理由を保持 |
| 観測中 | metrics inconclusive | 同じ段階をpause。欠測を0や成功に置換しない |
| Canary pause中 | resume | 新鮮なpassed観測と人の操作が必要。verdictをunknownに戻し再観測 |
| aborting | stable_routed | aborted。stable100%観測必須、Gitはまだ候補の可能性あり |
| aborted | restore_git | restoring、前版digestへの別承認Git変更 |
| restoring | restore_verified | restored。Git/実digest一致、2回再sync、代表CRUD合格 |

記載のない操作は拒否。状態内versionを単調増加し、毎操作CAS。
Git/runtime driftは進行操作を拒否する。安全なabortとその観測は許可する。
表示は進行中/成功/失敗/判定不能と理由を別fieldで持ち、abort完了とGit永続復元を区別する。
判定不能pauseから自動再開しない。Rollingの判定不能も完了せず保持し、再観測で判断する。

## 数値と証拠

SLIは[既存Account metrics契約](../canary/METRICS.md)を継承:
5分窓、各評価role20要求以上、2xx+3xx/全要求99%以上、250ms以下bucket/count95%以上、query timeout5秒。
4xxもerror、client timeoutは別項目。計数・率・latencyを新旧別に、実hash/source/digestと観測窓で対応付ける。
空、NaN、Inf、複数系列、timeout、部分Pod被覆、古いsampleは判定不能。有限の閾値違反は失敗。
全体query/窓が不正なら判定不能。窓が有効で、identity/鮮度/被覆/最低sampleを満たすroleの違反があれば、
他roleの欠測より失敗を優先する。違反と欠測の両理由を返し、`metrics_reason`に保持してabort後も表示する。
SDK counterはinstanceごとincrease後sumし、resetを処理。Pod数からtraffic割合を推定しない。
評価入力はwindow_start/end、role別last_sample_at、revision_hash/imageと、別途確認した期待identity・現在時刻を持つ。
5分窓の終端と最終sampleは未来不可・現在から90秒未満（既存SDK鮮度条件）。roleの期待hash/digest不一致は判定不能。
pauseは既存verdictを無効化し、pause後の新規観測なしのresume/promoteを拒否する。
旧metrics queryは10%時の1/9 Pod専用なので50/100へ流用しない。対象stageの実Pod被覆を別途照合する。
100%ではstable要求0が意図した経路。currentのcandidateだけ評価し、stableは直前の有効窓を時刻付き参考値にする。

実trafficの事前固定窓は[既存Istio手順](../istio/ROLLOUTS.md)を継承:
10%は1000件中candidate60〜140、50%は1000件中440〜560、100%は200/200、abortはstable200/200。
warmup20は測定から分離。HTTP失敗/未知revision0、逐次・新規接続・timeout2秒・retryなし。
これは限定学習の許容帯で、本番の統計的保証ではない。新たな閾値を根拠なく既定化しない。

## 代表5ケースと合格証拠

| ケース | 必要証拠 |
| --- | --- |
| 手動Canary正常 | 各10/50/100 pauseのGit/Application/RS/VS/DR/Envoy、実request count/error/latency、通常promoteの履歴 |
| 自動Canary正常 | 同じ3段階の実指標と判定理由、controller生成Run・hash対応、人手の成功書換えなし |
| 異常Abort/復旧 | 実閾値違反→stable100、主要CRUD、承認済み前版Git digest、2回再sync後一致 |
| metrics不足pause | 欠測/低sample/NaN/timeoutを判定不能表示、次段階へ進まず人の再開前に保持 |
| Rolling正常 | 固定digestの更新、Ready/API回帰/指標、Canary操作のUI非表示とAPI拒否 |

本課題の検証は`python3 -m unittest discover -s learning/account-release -p 'test_contract.py' -v`。
runtime5ケースの完成は後続機能・実環境証拠で確認する。PLAT-170は手動Canary/Abort統合だけ。
実credential・RBAC・security設定はこの文書によって承認されない。必要な操作を別途明示して停止する。
