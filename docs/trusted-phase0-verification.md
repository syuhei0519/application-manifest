# PE-003 protected-main検証（実受入待ち）

通常MRは静的test/render/schemaだけを行う。従来のMR内API/registry認証jobを置換し、保護mainから起動する固定Pythonコードへ移す。一般ブランチ名・template・default・migration・sidecar・init・release変更をinventoryの変更として扱う。configのみでimage/release不変なら由来照合だけ省略する。mainへの直接push/自動mergeは追加しない。

操作: 最新protected mainのweb/API pipelineにVERIFY_MR_IID（正の数値）を指定し、trusted-render-producerを明示実行する。serverのJob/ref/commit/pipeline、保護main、同一projectの開いたMRを照合し、source HEADを固定する。秘密なしnamespace内でrender/解析し、dotenvに非秘密の生成Job IDを渡す。後段はJob metadataと、そのJobの固定pathのartifact/checksumを独立API取得する。入力をGitから再照合し、未知repo/project、artifact/SHA/checksumの入替えを拒否する。

Phase 0の変更image/releaseについて、source pipelineのmain/success/SHA、成功publish-image/verify-imageのJob、publish artifactのdigest/source/project/pipeline、registry tag digest、linux/amd64 OCI config digest/revision/sourceを照合する。source mainが進んでいたら再提案。削除imageは過去由来を検証し、現在main一致は配備されないため要求しない。rollbackはrollback/local/ブランチと10文字以上のROLLBACK_REASONが必須で、鮮度だけを例外にする。DB互換性の実確認と人によるmain merge承認は別途必須。

新認証情報の必要な契約（2026-10-02包括承認のもと実設定済み）:

| 変数 | 用途/最小scope | 配置 |
|---|---|---|
| MANIFEST_VERIFY_READ_API_TOKEN | manifest/frontend/backendのJob/artifact/pipeline/main/MR読取。read_api。reader本人は対象projectの読取のみ | protected・masked/hidden・environment scope manifest-verification |
| FRONTEND_REGISTRY_READ_USER / PASSWORD | frontendだけread_registry | 同上 |
| BACKEND_REGISTRY_READ_USER / PASSWORD | backendだけread_registry | 同上 |

API認証をregistryへ転用しない。API読取は専用service accountのread_apiのみのPAT（2026-11-01期限、3projectのReporterだけ）。registryは各projectのread_registryだけのdeploy Token。管理PATを検証CIへ配置しない。producerは組込みCI_JOB_TOKENのGET /job、GET MR、GET branchesのみを使う。consumerのGET指定Jobにはread_apiが必要。[GitLab公式Job token権限](https://docs.gitlab.com/ci/jobs/ci_job_token/)。

API/JWT/manifestのredirectは拒否する。OCI config blobだけ、固定registryからの302/307に対し、https://cdn.registry.gitlab-static.net・標準HTTPS port・要求digestと完全一致するblobパス・userinfo/fragmentなし・URL長8192以下を確認して1回だけ取得する。CDN requestはAuthorization/Cookie/Refererなし。追加redirect/別host/別digestは拒否し、取得bytesのsha256をmanifestに束縛する。署名queryは改変・出力・保存しない。[GitLab公式CDN仕様](https://runbooks.gitlab.com/registry/cdn/)。

全MRのマージ条件は通常MR pipeline成功とprotected-main検証成功の両方を運用者が確認すること。GitLab標準の必須MR statusとして強制できると仮定しない。phase0-verification.jsonはmergeAuthorized=false。PE-004の直前3 SHA再確認とSHA指定mergeは未接続。生成Job/consumerの実CI、認証分離、AT-01の実registry照合はまだ未実施で、PE-003完了にはしない。Collector/Trivy/SBOM/Package公開のPhase 2処理は含まない。

CLIのAPI pipelineもprotected mainと正のVERIFY_MR_IIDだけを受け入れ、producerはmanual。pipeline変数上書きはOwnerのみ。生成Job 16884888152は実CIで成功し、consumerはCDN blob取得で停止した。現在の修正はこの実失敗を扱うもので、由来受入の完了は再試験後に記録する。
