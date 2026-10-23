# Account release MVP 受け入れ証跡（PLAT-171〜176）

2026-10-23 の専用 kind `plat171-account` / loopback Backstage による記録。対象は Account/local のみ。
固定 main は `e26d2b43140c64a602eecca7844f0c006ada3fc6`、実機で実行したコードは
`abed735b467c727a3d6941c6c1495d0f34e1bcc1`。14:12 UTC に実機受入と清掃を完了した。
本書は統合前の受入記録であり、main統合・統合後CI・Jira最終状態はPR43と最終証跡で照合する。

## コードと検証

- [PR43](https://github.com/syuhei0519/application-manifest/pull/43)：Canary/Rolling共通契約、Account限定のBackstage要求・操作・指標履歴、controller判定、Git前版復元。
- Python/Helm 66件、Portal 12件、typecheck、production build 成功。
- 同一コードheadの [PR CI](https://github.com/syuhei0519/application-manifest/actions/runs/39964325410) / [push CI](https://github.com/syuhei0519/application-manifest/actions/runs/39964317963) 成功。
- 独立レビューでモード切替、UI投影、bridge寿命の阻害指摘を解消。bridge全体120秒、個別外部コマンド15秒、判定窓300秒、証跡90秒を維持する。
- UI中断時のGitHub部分実行が発生したPR54/55は、operatorによる限定補完を記録し、UI-only成功に置き換えない。修正後のPR56/58/59/60/61は画面から同一要求IDで作成・再照合した。PR61は受付前の実際の画面再読込を挟み、保存IDから再開して受付・PR各1件を確認した。

## ACとの対応

| 課題 | 実証・回帰 | 状態 |
| --- | --- | --- |
| PLAT-171 | 固定digest・方式・manual/auto/null入力制約、非終端/古い世代の拒否。PR51/52でCanary→Rolling、PR53/54/55でRolling→Canary。旧image/hashを維持して段階別にGit/実状態を照合。Rolling readiness待ち、300秒期限超過、回復を実測。 | 合格（下記の証跡限界を明示） |
| PLAT-172 | Account/単一環境/許可digest、preview・cancel・review前非反映、要求ID・desired CAS・重複防止、保存IDによる再開。repo/path/ref、Application project/destination/namespace、認証・主体をサーバで限定。 | 合格 |
| PLAT-173 | manual 10/50/100%のPromote、Abort・stable回復、競合・stale・不明結果の読取り照合、重複操作拒否。UI/API停止中もauto controller進行。 | 合格 |
| PLAT-174 | auto正常3窓と異常1窓で新旧要求数・成功率・250ms比率を同じwindow_endでPrometheusと厳密照合。UIは同じcontroller判定/窓/理由/履歴、実Rollout revisionとPod hash、release ID/generation/元要求ID、error率を表示する。欠測・不正値を正常表示しない。 | 合格。過去窓再照会差は下記に保持 |
| PLAT-175 | 正常10/50/100%自動完了、異常自動Abort、低traffic Inconclusive/Pausedと60秒間の無再試行。fixtureはNaN・query timeout・不足等を含む。 | 合格（正常・異常・欠測・低traffic） |
| PLAT-176 | PR47/57/59/61で、freshなstable回復証跡からレビュー済み前版Gitへ復元。Git/Argo/実image/通信を照合し、Account・Rollouts・ArgoCDの所有Pod再起動と同Git再sync後も維持。 | 合格（最終復元・排他解除を含む） |

## 実測

- manual正常：PR45、10/50/100%各300秒窓、Promoteと排他解除、API12件。
- manual Abort：PR46。Git desiredの新版と実trafficのstableを区別し、PR47で永続復元。
- 自動正常：PR55 merge `a86bbd2286287c558d93f36d8256845b4b079eb6`。1100秒7053件HTTP200、各判定は300秒。UI/API停止を挟んでもcontrollerが進行し、最終100%成功・API12件・要求完了を確認。
- 自動異常：PR56 merge `2772aa46bb36b040f4c766ea9dbd714c086e321d`。330秒2124件HTTP400、新旧可用性0%で12:39:21 UTCに自動Abort。stable Bへ200通信・API12件回復。PR57 merge `a07dea02477cd0642b3fa78aff5b52a0a00ab32e`でGit復元・再起動維持・画面の永続復元完了を確認。
- 低traffic：PR58 merge `49a7ca1b0dae2372634170fff81c68476b55ba2b`、gen13、AR `account-release-f9c59c964-9-1`。90/10配分を確認して10.134秒65件HTTP200だけ送信。300秒後、候補raw増分0（20未満）・判定用3指標欠測でInconclusive。Paused/step1/同一AR・Git・配分が60.557秒不変。手動Abort、API12件回復後、PR59 merge `1888de63c781320b92fcbcca4f7c3cb035eeef72`でGit復元・再起動・API/200件通信・永続復元完了を確認。
- 欠測：PR60 merge `72f690117cb05d522abeec317bc7f7bb9feaa9fd`、gen15、AR `account-release-f9c59c964-11-1`。13:34:33–13:39:33 UTCの300秒窓で、専用collectorの330秒停止により新旧3指標が欠測。同窓Prometheusの6つの空応答とUI/controller値を照合。Inconclusive/Paused、同一AR/Git/配分、無再試行を60.507秒確認。collectorは同一UID/specでReadyへ復帰。Abort `901e9ea8-a6d0-42e1-8c4a-631f7ec30901` とAPI12件回復が成功。
- 最終永続復元：PR61 head `f45d26f52259adbeec317afa62b7204f1da62b93`、独立レビュー・PR CI 39965011949・push CI 39965004854成功、14:01:33 UTCにtrial merge `7d604a1ae3949515c572cd8d2fe855d8c0d42c2d`。画面から1回反映しgen16/B Healthy・Git一致。Account2Pod、Rollouts/ArgoCD各所有Podを再起動し、同Git再sync後もAPI12件・200件通信が成功。receipt SHA256 `7b8c5e5a285aa2a9cc973bac563de4b9b7de56704cecee60d52cc0394a25779b`。14:11:48.132 UTCに要求 `df8cbecd-30ff-425b-8d20-0a9d0d03b1a3` が `restored / durable-restore-verified`、画面「永続復元完了」、排他解除を確認した。

## 証跡の限界

- 低traffic窓のstable要求数は、controller記録73.47525385452633、後の同窓再照会68.89183140163625で不一致。厳密照合の失敗を保持する。生系列の末尾1サンプル有無と`increase`外挿で両値を完全再現できるが、当時の取り込み時刻がないため後着sample発生の断定はしない。画面と元controller記録の一致、停止分岐の確認と区別する。1〜19件の候補trafficを実観測したとは主張しない。
- Rolling期限超過瞬間の画面撮影は未取得。実機raw projectionと同じ表示部品のSSR回帰を保存しており、live撮影の代替成功とは扱わない。
- 試行失敗PR48、operator基準点PR50、operator補完PR54/55、stale/期限切れで拒否された要求を成功履歴へ書き換えない。
- 依存ライブラリ警告43件は残る。scanの方針合格を脆弱性ゼロと同義にしない。

## 保存場所と終了条件

PCの作業ディレクトリ `evidence/plat171-176-session3` に、各PRの固定head/base/差分/独立レビュー/CI/merge、raw Kubernetes/AnalysisRun、同窓Prometheus応答、通信・API、receipt SHA256を保存する。秘密は`backstage-private`と専用kubeconfigに分離し、Git・会話・公開証跡へ出力しない。

14:12:24.996 UTCに専用kind/node、所有Backstageプロセス323528/323540/323575、全専用token/kubeconfig/署名鍵/試験DBを削除した。ポート3000/7007の閉鎖、既存kind `learning-20261018-69c763` のnodeと既定context不変、ソース・証跡・バックアップ保持を確認した。最後のtokenは13:18:23発行/14:18:23失効予定で、自動更新・追加発行はしていない。

実機ACは独立レビューで追加blockingなし。低traffic事後再計算差、期限瞬間のlive未撮影、operator補完を伴う過去試行は上記のまま保持する。main統合と統合後CI、Jira最終照合は本書保存後の手順であり、この時点ではDoneにしていない。
