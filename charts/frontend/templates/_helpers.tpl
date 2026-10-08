{{/*
frontend template間で共有するlabel等の定義。includeから呼ばれ、生成YAMLの断片を返す。
*/}}{{- define "frontend.labels" -}}
app.kubernetes.io/name: frontend
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: frontend
app.kubernetes.io/part-of: account
app.kubernetes.io/managed-by: Helm
{{- end }}
