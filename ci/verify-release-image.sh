#!/bin/sh
# CI/運用検証の入口。.gitlab-ci.ymlが指定する固定入力と検証器を使用する。
# 非成功終了は後続の検査を止める。成功してもcluster変更・merge権限を自動付与するものではない。
set -eu

file=${1:?values file is required}
token=${MANIFEST_VERIFY_TOKEN:?MANIFEST_VERIFY_TOKEN is required}

value() {
  section=$1
  key=$2
  sed -n "/^${section}:/,/^[^[:space:]]/s/^[[:space:]][[:space:]]${key}:[[:space:]]*\"\([^\"]*\)\"[[:space:]]*$/\1/p" "$file"
}

repository=$(value image repository)
tag=$(value image tag)
expected_digest=$(value image digest)
commit=$(value release sourceCommit)

test -n "$repository"
test -n "$tag"
test -n "$expected_digest"
test -n "$commit"

registry=${repository%%/*}
path=${repository#*/}
test "$registry" = registry.gitlab.com
source_url="https://gitlab.com/$path"
image="$repository:$tag"

crane auth login -u oauth2 -p "$token" "$registry" >/dev/null 2>&1
actual_digest=$(crane digest "$image")
test "$actual_digest" = "$expected_digest" || {
  echo "registry digest does not match $file" >&2
  exit 1
}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
crane config "$image" > "$work/config.json"
tr -d '[:space:]' < "$work/config.json" > "$work/config.compact.json"
grep -Fq "\"org.opencontainers.image.revision\":\"${commit}\"" "$work/config.compact.json" || {
  echo "OCI revision label does not match $file" >&2
  exit 1
}
grep -Fq "\"org.opencontainers.image.source\":\"${source_url}\"" "$work/config.compact.json" || {
  echo "OCI source label does not match $file" >&2
  exit 1
}
