# PE-003A 全PodSpec inventoryと秘密なしrender

ブランチ名やvalues差分ではなく、固定target SHAとsource SHAのmerge candidate treeをrenderする。tools/inventoryはPod/Deployment/StatefulSet/DaemonSet/ReplicaSet/ReplicationController/Job/CronJobのcontainers/initContainers/ephemeralContainers、PodTemplateのaccount.lab release参照を列挙する。restartNonceだけはrelease由来変更ではない。未知kind/未解析/未固定digest/重複identityは停止する。別repositoryはinventory差分に現れ、許可/由来判定はPE-003Bで拒否する。

ci/trusted-render.pyは運用者側で実行する固定検証器。Git ls-tree/cat-fileでregular blobだけをコピーし、symlink/submodule/巨大入力を拒否する。MRのスクリプトを実行せず、trusted verifier SHAのvendored Go解析器を固定Go image内でcompileする。Helm/解析はDockerのnetwork=none/read-only/cap-drop/no-new-privileges/CPU2/memory512Mi/PID64/90秒/output2MiBで隔離する。compilerだけmemory1Gi/tmpfs512Miを使い、renderはtmpfs64Mi。job token/認証情報/環境変数/host socketをcontainerへ渡さない。Chart plugin/post-renderer/dependency download/hook実行を禁止し、hookはYAMLデータだけとして解析する。

運用者はorigin/mainに統合された固定verifier SHAからscriptをGit外へ取り出し、その固定コードを実行する。script自身と固定SHAが一致しない場合は拒否する。運用者のローカル実行はtest-onlyを必須にし、eligibleForAuthenticatedVerification=falseを出力する。非test出力はprotected main/検証器SHA/実生成job IDのCI環境一致を要求する。PE-003Bで生成jobのGitLab APIとartifact対応を独立確認するまで、この自己申告flagだけでマージ許可に使わない。現在のKubernetes RunnerにはDocker socketを追加せず、秘密なし生成jobの実結合は次のPE-003Bで受入する。

```sh
python trusted-render.py --repo /absolute/application-manifest --target FULL_SHA --source FULL_SHA --verifier TRUSTED_MAIN_SHA --producer-job-id JOB_ID --output /absolute/evidence/binding.json
```

出力はtarget/source/verifier/candidate tree、固定tool、全入力hash、render/inventory checksum、生成job ID、変更前後inventoryを持つ。image/releaseが不変でもrenderは常に実施する。provenanceRequiredはimage/releaseの変更検知だけを表し、由来合格を表さない。PE-003Bは固定jobからこの出力を取得し、schema/SHA/checksum/job対応とrepository/project/OCI由来を検証する。MR自己申告artifact/URL/検証器を秘密付き工程で信頼しない。

現在のlegacy MR認証jobの置換とprotected main manual pipeline結合はPE-003Bで行う。通常MR CIだけでimage MRをマージせず、両側検証gateの受入完了を待つ。AT-01のparser fixture成功と実Git merge candidateのsandbox証跡を区別し、API/registry失敗/allowlist拒否/改ざん受入まで終えてAT-01全体を合格とする。
