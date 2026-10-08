{{/*
PostgreSQL template共通labelを定義する。
呼出元はcharts/postgresql、接続設定はcharts/backend/templates/_helpers.tpl。
*/}}{{- define "postgresql.labels" -}}
app.kubernetes.io/name: postgresql
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: database
app.kubernetes.io/part-of: account
app.kubernetes.io/managed-by: Helm
{{- end }}
