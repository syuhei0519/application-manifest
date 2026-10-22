# Handoff

## 目的
- Source: jira-native
- Jira Site: https://syuhei0519.atlassian.net
- Jira Cloud ID: 0e029d88-1812-4bd9-b5de-bdecfff6740b
- Jira Project: PLAT
- Jira ID: 10181
- Jira: PLAT-171
- Jira URL: https://syuhei0519.atlassian.net/browse/PLAT-171
- Source GitHub: none
- Target Repo: syuhei0519/application-manifest
- Base Commit: e26d2b43140c64a602eecca7844f0c006ada3fc6
- Authorization: 2026-10-22 人による PLAT-171〜176 実装開始の明示承認

### AC
* PLAT-169の入力schemaに固定image digest、strategy、Canary promotion modeを定義し、Helm/Git desired stateとruntimeの対応を検証する。
* Canary manual/autoを明示的に選べる。Rollingではpromotion modeと段階promote/pauseの入力・操作を拒否し、UIも表示/有効化しない。
* 進行中、停止待ち、復旧中など未終端のreleaseでは方式変更を拒否する。古いgeneration/versionに対する要求も拒否する。
* 終端状態から方式を変更してもrevision/現在版/前版のidentityが失われず、残ったCanary resource/traffic設定がRollingを妨げない。
* Rollingのreadiness・進行失敗・完了を実状態から表示し、Canary成功判定を流用しない。

## やったこと
現行 Jira 全文・AC・依存を照合。統合済み PLAT-169/170 を再利用し、本リポジトリの共通契約・adapter fixture と chart の担当部分を実装する。

## 観察結果
- AC Evidence: pending — 実装、独立レビュー、最終CI、必要な実環境/UI検証と統合は未完了。
- Unapproved Operations: merge, deploy, credentials, permissions
- Stop Reason: none
承認不要の本リポジトリ変更のみ開始可能。Backstage 基盤の所在/不足、実環境用操作への今回の承認は別境界として保留する。これらを検証済みとはしない。

## 学び
作成時の共通実行境界は履歴として保持。2026-10-22 の今回承認は対象6件のrepo-change/fixture-test/draft-prのみ。別課題・Sprint・merge・前回環境許可へ拡張しない。


## 静的実装の追記（2026-10-22）

branch: `feat/plat-171-176-account-release`。171/175の共有chart・controller判定を一つのdraft PRでレビューする。
変更とACごとの未実施項目は [RELEASE-MODES.md](RELEASE-MODES.md) に記録。
Python/Helm 24件、Go11テスト関数と分岐、固定upstream schemaを検証。最新PromQL差分の独立レビューとCIはPRで照合する。
独立レビューで対象identity・未収束終端化・切替適用順・Pod/SDK/desired template照合を修正した。
実環境AC・Backstage UI・操作adapter・main統合は未完了。新環境・credential/RBAC/配備・mergeの許可は含めない。
