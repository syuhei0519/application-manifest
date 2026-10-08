#!/bin/sh
# CI/運用検証の入口。.gitlab-ci.ymlが指定する固定入力と検証器を使用する。
# 非成功終了は後続の検査を止める。成功してもcluster変更・merge権限を自動付与するものではない。
set -eu

file=${1:?values file is required}
token=${MANIFEST_VERIFY_TOKEN:?MANIFEST_VERIFY_TOKEN is required}
api=${CI_API_V4_URL:-https://gitlab.com/api/v4}
project=$(yq -r '.release.sourceProjectId' "$file")
commit=$(yq -r '.release.sourceCommit' "$file")
pipeline=$(yq -r '.release.pipelineId' "$file")
repository=$(yq -r '.image.repository' "$file")
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

attempt=0
while :; do
  wget -qO "$tmp/pipeline.json" --header="PRIVATE-TOKEN: $token" "$api/projects/$project/pipelines/$pipeline"
  status=$(yq -r '.status' "$tmp/pipeline.json")
  case "$status" in
    success) break ;;
    failed|canceled|skipped|manual)
      echo "source pipeline is not successful: $status" >&2
      exit 1
      ;;
  esac
  attempt=$((attempt + 1))
  test "$attempt" -lt 12 || {
    echo "source pipeline did not finish successfully within 60 seconds" >&2
    exit 1
  }
  sleep 5
done
test "$(yq -r '.sha' "$tmp/pipeline.json")" = "$commit"
wget -qO "$tmp/branch.json" --header="PRIVATE-TOKEN: $token" "$api/projects/$project/repository/branches/main"
test "$(yq -r '.commit.id' "$tmp/branch.json")" = "$commit"

case "$repository" in
  registry.gitlab.com/*) ;;
  *) echo "unsupported image repository: $repository" >&2; exit 1 ;;
esac
