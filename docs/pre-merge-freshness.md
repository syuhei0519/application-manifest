# PE-004 マージ直前の固定読取検査

ci/pre-merge-check.shはreview済みprotected mainから実行する。read_api専用TokenとconsumerJobId/verifierShaをstdin JSONで渡し、秘密値を引数・環境・ファイルへ置かない。checker自身とAPI依存を固定Git blobに照合し、成功consumer/producerのserver Job/ref/SHA/pipelineを検査する。

MR source、manifest target main、frontend/backend mainを検証reportと比較する。source/target/appいずれのSHA変化でも停止し、最新組合せで生成とconsumerを再実行する。読取checkerはmergeAuthorized=falseを返し、merge credentialを持たない。提案botはDeveloper（30）、mainはpush No one、merge Maintainer（40）以上に保護されている。

Ownerの操作は1件ずつ、開始時checker→API直前の3種類のSHA再照合→Merge APIへsource HEAD sha指定→成立commit/treeをcandidateTreeに照合する。Merge APIのshaはsourceだけのCASでありtarget mainのCASではない。最後のGETと成立までtarget/appに残存競合がある。成立treeが異なれば完了扱いにせず、main再検証と復帰判断を行う。

## 2026-10-02 AT-02の実受入

manifest main更新、MR20のサーバーrebaseによるsource更新、backend/frontend mainの別々の更新を実APIで観測し、古いreportを拒否した。最新mainで生成・consumerを再実行し、MR25/26を順番に統合した。各MRのsource SHAを全ゼロへ入れ替えたMerge APIは409となり、MRがOpenのままであることを確認した。直前の3種類のSHAが一致してから正しいsource SHAで統合し、両方の成立Git treeがcandidateTreeと一致した。

- backend: MR25、protected main pipeline2905216036、producer16885585333、consumer16885585334。
- frontend: MR26、protected main pipeline2905226432、producer16885649061、consumer16885649062。
- source再生成: pipeline2905190318。全ジョブ成功と最新source照合を確認。
- target再生成: pipeline2905186709。全ジョブ成功と最新target照合を確認。

固定source/build artifact/immutable digest/OCI labelのPhase 0由来を検証した。scan/SBOMは未導入のためこの受入には含めない。実測はdocs/evidence/PE004-AT02.jsonを参照する。ローカル/CIの4テストはsource/target/各app更新、closed/fork/非main/未保護、Job/ref/SHA/pipeline入替えを別途拒否する。