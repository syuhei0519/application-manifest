{{/* 学習用の各 API と専用 DBの描画で、複数のテンプレートで使う名前・ラベル・設定の組み立てをまとめます。 */}}{{- define "learning.name" -}}learning-{{ .Values.service }}{{- end -}}
{{- define "learning.database" -}}
{{- get (dict "account" "account" "order" "orders" "payment" "payments" "notification" "notifications") .Values.service -}}
{{- end -}}
{{- define "learning.source" -}}{{ index .Values.sourceCommits .Values.service }}{{- end -}}
{{- define "learning.image" -}}
local.learning/{{ .Values.service }}:{{ include "learning.source" . }}-{{ trimPrefix "sha256:" .Values.imageConfigID }}
{{- end -}}
{{- define "learning.dbImage" -}}
local.learning/postgresql:{{ trimPrefix "sha256:" .Values.database.imageConfigID }}
{{- end -}}
{{- define "learning.labels" -}}
app.kubernetes.io/part-of: learning-only
learning.local/owner: {{ .Values.owner | quote }}
learning.local/service: {{ .Values.service | quote }}
{{- end -}}
{{- define "learning.annotations" -}}
learning.local/source-commit: {{ include "learning.source" . | quote }}
learning.local/image-config-id: {{ .Values.imageConfigID | quote }}
{{- end -}}
{{- define "learning.env" -}}
- {name: PGHOST, value: {{ printf "%s-db" (include "learning.name" .) | quote }}}
- {name: PGPORT, value: "5432"}
- {name: PGDATABASE, value: {{ include "learning.database" . | quote }}}
- {name: PGUSER, value: {{ printf "%s_app" .Values.service | quote }}}
- {name: PGSSLMODE, value: disable}
- {name: LOG_LEVEL, value: info}
- name: PGPASSWORD
  valueFrom:
    secretKeyRef: {name: {{ include "learning.name" . }}-db, key: app-password}
{{- end -}}
{{- define "learning.apiEnv" -}}
{{- include "learning.env" . }}
{{- if .Values.telemetry.enabled }}
{{- if ne .Values.service "account" }}{{ fail "telemetry is Account-only" }}{{ end }}
- {name: OTEL_EXPORTER_OTLP_ENDPOINT, value: "http://otel-collector.observability.svc.cluster.local:4318"}
- {name: DEPLOYMENT_ENVIRONMENT, value: local}
# Legacy SDK compatibility input, NOT the real deployment namespace.
- {name: POD_NAMESPACE, value: account}
- name: POD_UID
  valueFrom:
    fieldRef: {fieldPath: metadata.uid}
{{- end }}
{{- end -}}
