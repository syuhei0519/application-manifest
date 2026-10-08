#!/bin/sh
# CI/運用検証の入口。.gitlab-ci.ymlが指定する固定入力と検証器を使用する。
# 非成功終了は後続の検査を止める。成功してもcluster変更・merge権限を自動付与するものではない。
set -eu

base=${CI_MERGE_REQUEST_DIFF_BASE_SHA:?CI_MERGE_REQUEST_DIFF_BASE_SHA is required}
branch=${CI_MERGE_REQUEST_SOURCE_BRANCH_NAME:?CI_MERGE_REQUEST_SOURCE_BRANCH_NAME is required}
if [ -n "${CHANGED_FILES:-}" ]; then
  changed=$CHANGED_FILES
elif command -v git >/dev/null 2>&1; then
  changed=$(git diff --name-only "$base" HEAD)
else
  token=${MANIFEST_VERIFY_TOKEN:?MANIFEST_VERIFY_TOKEN is required}
  api=${CI_API_V4_URL:-https://gitlab.com/api/v4}
  project=${CI_PROJECT_ID:?CI_PROJECT_ID is required}
  head=${CI_COMMIT_SHA:?CI_COMMIT_SHA is required}
  comparison=$(mktemp)
  trap 'rm -f "$comparison"' EXIT
  wget -qO "$comparison" --header="PRIVATE-TOKEN: $token" \
    "$api/projects/$project/repository/compare?from=$base&to=$head"
  changed=$(yq -r '.diffs[].new_path' "$comparison")
fi
case "$branch" in
  update/local/frontend/*) expected=environments/local/frontend.yaml; service=frontend ;;
  update/local/backend/*) expected=environments/local/backend.yaml; service=backend ;;
  bootstrap/local/initial-images) expected='environments/local/frontend.yaml environments/local/backend.yaml'; service=initial ;;
  rollback/local/*) exit 0 ;;
  *) exit 0 ;;
esac

for file in $changed; do
  case " $expected " in *" $file "*) ;; *) echo "image update changes forbidden file: $file" >&2; exit 1 ;; esac
done
for file in $expected; do
  echo "$changed" | grep -qx "$file"
  tag=$(yq -r '.image.tag' "$file")
  digest=$(yq -r '.image.digest' "$file")
  commit=$(yq -r '.release.sourceCommit' "$file")
  project=$(yq -r '.release.sourceProjectId' "$file")
  pipeline=$(yq -r '.release.pipelineId' "$file")
  pipeline_url=$(yq -r '.release.pipelineUrl' "$file")
  echo "$tag" | grep -Eq '^[0-9a-f]{40}$'
  echo "$digest" | grep -Eq '^sha256:[0-9a-f]{64}$'
  test "$tag" = "$commit"
  echo "$project" | grep -Eq '^[1-9][0-9]*$'
  echo "$pipeline" | grep -Eq '^[1-9][0-9]*$'
  echo "$pipeline_url" | grep -Eq '^https://gitlab\.com/.+/-/pipelines/[1-9][0-9]*$'
done
