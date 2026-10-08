# PE-007 Phase 0 rollback運用と実受入

2026-10-02、PE-007A/Bを受入。対象はPhase 0の成功main image証跡を使うGit rollbackとDB互換性・保護・復元。Phase 2のOCI evidence、scan/SBOM再検証、AT-14は未実施でPE-018/019へ残す。

## 操作契約

rollback/local/<service>/... branchを現在のapplication-manifest mainから作成し、選択サービスのimage tag/digestと元成功main pipeline/publish job等のrelease provenanceだけを戻す。ConfigMap、nonce、他サービスの変更を混入させない。ROLLBACK_REASONを10文字以上で記録し、protected mainの固定CI設定から検証する。理由とbranch prefixの両条件がある場合だけ、選択サービスのsource main freshnessを免除する。成功main pipeline、commit、元artifact、OCI labels、registry digest、image identity、MR source/target・他サービス鮮度は免除しない。

merge直前にMR source、manifest main、両アプリmainを再確認し、不一致なら更新・再検証する。MR source SHA付きmergeを使い、候補merge treeと実merge treeを照合する。最後のGETとmerge間のtarget/app更新競合はAPI source CASだけでは完全に排除できないため、照合失敗を成功として扱わない。

DB schema対応範囲を旧backendの起動/readinessと実CRUDで確認する。互換性がなければimageを戻すだけでは復旧しない。migration downやDB/PVC/data削除を自動実行しない。担当者が復元対象、backup checksum、schema history/data、許可された復元先を確認する。Application全体を固定revisionでsyncし、既存operation stateの再利用を避ける。復旧後は通常の最新main image検証を通して前進し、CRUDと履歴不変を確認する。

## PE-007A実結果

旧backend commit 2be970a2705de6baa1e165c3e6fb866c304dda3a、digest sha256:6aee0f5420cb43ca71407d80011db793530e1e054dc8158e61dd02f10f8189d1、元成功main pipeline 2902409013 / publish job 16874999517を使用。

隔離DB schema 2では旧backend livez=200、readyz=503、Pod Ready=false、Service ready endpoints=0。隔離DBだけをschema 1へ戻すと同Podのreadyz=200、旧image CRUD成功。実accountのschema historyは変更しない。

MR !38の理由なし検証2905562819ではrender成功後、consumerが `source main advanced; regenerate the proposal` で拒否。理由付き2905570291で元provenanceとdigestを検証し、選択サービスだけfreshness免除。誤source SHA mergeは409、正sourceで統合し候補/実tree一致。両kindへ同期、隔離accountで実旧digest、Healthy/Synced、CRUDとschema history timestamp不変を確認。

MR !39は最新backend source mainを通常freshnessRequired=trueで検証・統合し、同じDBで最新digest/CRUDを確認。MR !29で最新frontendへ復帰し、両workloadの実imageID、Healthy/Synced/Succeeded、frontend経由CRUD成功。!36のmerge conflictによるproducer失敗は鮮度拒否の受入証跡に含めない。!30は!39に置換してClosed。

## PE-007B実結果

platform-gitops MR !10の実受入で、別の隔離kindへbackup checksum、全records、schema history、既知rowの一致を確認。DB prune confirmとPVC prune=falseを実測し、承認した隔離DB prune/recreateでも同PVCとdata markerを保持。Application cascade deleteのconfirm待ちでDB/PVC/data保護を確認。直接kubectlを実行する操作者の権限は別であり、Argo annotationだけで全削除を禁止したと主張しない。

[秘密値なし実証跡](../evidence/PE007-rollback-runtime.json)。PW、Secret data、認証header、SQL backup内容は含めない。試験用kind/DB/PVC/dataと専用kubeconfigは最終監査後に削除し、SourceLabは保持する。