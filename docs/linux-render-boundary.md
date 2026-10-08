# PE-003 Linux render境界（CI接続作業中）

固定Go imageの/usrだけを渡し、user/mount/PID/network namespace内で、読取専用Git snapshotをHelm render/Go解析する。親の認証env、/proc、repository/.git、socket、kubeconfig、CA/proxy設定を渡さない。固定Helm archiveのSHA256を取得前後に照合する。生成報告にはarchive/binary checksum、cgroup CPU/memory/PID bounds、対象/source/verifier SHAを記録する。

実測: 2 CPU/2Gi/PID128のコンテナで実render PASS。保存済みboundary testは、資格情報の非継承、外向き通信不可、入力と/usrの読取専用、private /proc、期限、出力量停止の3件PASS。非秘密の目印のみ使用した。

これはtest-only成果物であり、認証付き由来検証に利用できない。protected-main生成JobのAPI由来ID/SHAとconsumerの独立照合はコード接続済みだが、実CI受入は未実施。CPU2/メモリ2Giを超える/上限なしのRunnerではfail closedで動作しない。PID上限は実cgroup値を記録し、上限なしならnullとする。namespaceはPID数の制限ではない。Docker試験のPID128を実Runnerにも存在すると主張しない。設計§5.5のCPU/時間/出力上限を必須とし、稼働Runner/kubeletの設定をこの変更で変更しない。

再現は固定Go image内で `python3 -I ci/namespace_boundary_test.py -v` を実行する。コンテナはUID/GID1000、CPU2、memory2g、pids-limit128、既存Runnerに合わせたUnconfined seccomp/AppArmorを要求する。Docker socketをCIへ追加する方式ではない。通常MRのコードへ認証情報を渡す工程の代替が完成したとは扱わない。
