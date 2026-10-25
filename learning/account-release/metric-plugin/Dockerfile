# Argo Rollouts が呼び出す計測 plugin の実行イメージを作ります。
# The build context contains only the reviewed linux/amd64 account-policy binary.
# Build it with the pinned Go builder, CGO_ENABLED=0, -trimpath and -buildvcs=false.
FROM quay.io/argoproj/argo-rollouts:v1.10.1@sha256:fbcc4cf0a29da25d86230992b1dfdeadf30b6f1baa74ea7c9c98e3e580769396
COPY --chmod=0555 account-policy /opt/account-policy
