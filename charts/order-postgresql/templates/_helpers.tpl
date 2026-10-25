{{/* Order 専用 PostgreSQLの描画で、複数のテンプレートで使う名前・ラベル・設定の組み立てをまとめます。 */}}{{- define "order-postgresql.labels" -}}
app.kubernetes.io/name: order-postgresql
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: database
app.kubernetes.io/part-of: order
app.kubernetes.io/managed-by: Helm
{{- end }}
