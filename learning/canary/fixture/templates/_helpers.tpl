{{- define "learning.labels" -}}
app.kubernetes.io/part-of: learning-only
learning.local/owner: {{ .Values.owner | quote }}
learning.local/service: "account"
{{- end -}}
