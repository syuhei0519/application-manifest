{{/* Canary 解析用の試験 providerの描画で、複数のテンプレートで使う名前・ラベル・設定の組み立てをまとめます。 */}}{{- define "learning.labels" -}}
app.kubernetes.io/part-of: learning-only
learning.local/owner: {{ .Values.owner | quote }}
learning.local/service: "account"
{{- end -}}
