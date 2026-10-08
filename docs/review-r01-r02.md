# R01 / R02 独立レビューへの修正

2026-10-07。対象レビューは `core-platform-v1-independent-review.md` の R01 / R02。
両指摘は妥当。以前の成功した配備・負荷試験の実行結果は書き換えず、
今後の配備 MR を受け入れる保護 main のゲートを修正する。

## R01: 古い合格一式の再選択

従来の native Go verifier は選択した run 内の record / scan / SBOM / writer を
照合していたが、別 run の後続失敗は入力に含まれなかった。

`ci/trusted_attempts.py` を manifest の保護 main consumer と pre-merge checker
の両方へ接続した。対象アプリ project、policy revision、保護 main の pipeline
一覧と scan job 履歴（retry を含む）を API から取得する。MR が最新 attempt を
自己申告しても採用せず、実際の開始時刻と job ID で選択 run と比較する。
同一 digest の後続実行があれば、整合した古い成功一式でも拒否する。
後続も成功なら、その新 run 一式を明示選択して既存 native 検証を通す必要がある。

後続の識別は成功した保護 main writer の固定 safe JSON、record checksum、
project / policy / scan job / immutable URL の対応を使う。失敗・実行中・証跡不足を
古い成功への fallback で解消しない。別 digest と確認できた run は対象外とする。
未実行の manual job は attempt と扱わない。古い pipeline の manual 実行や
複数ページの後段も照合する。

ゲートは pipeline 最大 1,000 件・各 job 一覧 100 件未満に限定し、読み取り失敗、
権限不足、期限切れ artifact、不完全な一覧では拒否する。必要な証跡は保持する。
trace、private OCI binary、pipeline 変数、資格情報の再発行は使用しない。
アプリ側の Go record reader は個別 run の由来検証を担うもので、単体で
manifest 配備を許可するゲートではない。今回の最新 attempt 照合は配備を採否する
共通 manifest ゲートへ集約し、frontend / backend と rollback の両方に適用する。

## R02: 時間経過による失効

native 検証が照合した record と同じ checksum の writer bytes から、scan 時刻、
DB 更新時刻、適用例外の期限を取得する。consumer report の各選択にこれらと
`validUntil` を結合する。全体の期限は各選択の期限と report 作成後 24 時間の最短。

pre-merge は現在 UTC で期限を再計算し、scan + 24h、DB + 24h、例外期限の
いずれかに到達すれば SHA が不変でも拒否する。古い形式の report、未来日時、
期限の延長・不整合も拒否し、再検証を要求する。rollback でも期限を免除しない。
R01 の後続 attempt もマージ直前に live API で再照合し、consumer 成功後に
追加された失敗を見逃さない。出力する pre-merge 結果にも期限を含める。

固定 main の依存ファイル byte 照合に新 helper を含める。従来どおり checker は
読み取り専用で `mergeAuthorized=false`、Owner の SHA 指定 merge は別操作。
直前確認と merge は外部操作なので原子的なロックとは主張しない。

## 検証

既存の Linux Python 環境で、通信しない次の 8 suite、合計 **44 tests 成功**。

- trusted_identity_test / trusted_contract_test / trusted_registry_test
- pre_merge_check_test / trusted_attempts_test
- trusted_phase2_test / trusted_binary_test / trusted_consumer_failure_test

追加・更新した fixture では A 成功 → B 失敗 → A 一式の再選択、同一 pipeline、
古い pipeline の後続 manual、複数ページ、後続成功への明示切替、別 digest、
未実行 manual、証跡欠落、実行中、checksum 差替えを確認した。
scan / DB / 例外それぞれの期限前・到達後、SHA 不変、古い report、未来日時、
期限延長、pre-merge での live 再照合も確認した。

Windows での全 suite discovery は既存 Linux 専用 `resource` import で停止するため、
その結果を成功扱いしていない。今回変更しない namespace の実境界試験は再実行していない。

本修正の検証では新 CI、OCI 取得、レジストリ公開、負荷試験、配備変更をしていない。
ローカル修正とオフライン回帰成功は、main 統合・実 CI 成功を意味しない。
既存の PE019 受入原本・失敗記録・レビュー本文を上書きしていない。
