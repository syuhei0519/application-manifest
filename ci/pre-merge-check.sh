#!/bin/sh
# CI/運用検証の入口。ci/pre_merge_check.pyが固定mainの由来・期限を読む。
# 非成功終了は後続の検査を止める。成功してもcluster変更・merge権限を自動付与するものではない。
set -eu
exec python3 -I "$(dirname "$0")/pre_merge_check.py"
