{{/* Account の Canary 配備と解析の描画で、複数のテンプレートで使う名前・ラベル・設定の組み立てをまとめます。 */}}{{- define "learning.name" -}}learning-account{{- end -}}
{{- define "learning.database" -}}
account
{{- end -}}
{{- define "learning.source" -}}{{ .Values.sourceCommit }}{{- end -}}
{{- define "learning.image" -}}
local.learning/account:{{ include "learning.source" . }}-{{ trimPrefix "sha256:" .Values.imageConfigID }}
{{- end -}}
{{- define "learning.labels" -}}
app.kubernetes.io/part-of: learning-only
learning.local/owner: {{ .Values.owner | quote }}
learning.local/service: "account"
{{- end -}}
{{- define "learning.annotations" -}}
learning.local/source-commit: {{ include "learning.source" . | quote }}
learning.local/image-config-id: {{ .Values.imageConfigID | quote }}
{{- end -}}
{{- define "learning.env" -}}
- {name: PGHOST, value: {{ printf "%s-db" (include "learning.name" .) | quote }}}
- {name: PGPORT, value: "5432"}
- {name: PGDATABASE, value: {{ include "learning.database" . | quote }}}
- {name: PGUSER, value: "account_app"}
- {name: PGSSLMODE, value: disable}
- {name: LOG_LEVEL, value: {{ if eq .Values.revision "good" }}info{{ else }}warn{{ end }}}
- name: PGPASSWORD
  valueFrom:
    secretKeyRef: {name: {{ include "learning.name" . }}-db, key: app-password}
{{- end -}}
{{- define "learning.apiEnv" -}}
{{- include "learning.env" . }}
{{- if .Values.telemetry.enabled }}
- {name: OTEL_EXPORTER_OTLP_ENDPOINT, value: "http://otel-collector.observability.svc.cluster.local:4318"}
- {name: DEPLOYMENT_ENVIRONMENT, value: local}
# Legacy SDK compatibility input, NOT the real deployment namespace.
- {name: POD_NAMESPACE, value: account}
- name: POD_UID
  valueFrom:
    fieldRef: {fieldPath: metadata.uid}
{{- end }}
{{- end -}}
