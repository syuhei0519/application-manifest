{{/*
backendの共通label/env生成。Deploymentとmigration Jobが同じbackend.envをincludeする。
非秘密値はvaluesからPG環境へ、app資格情報はSecretKeyRefから個別に注入する。
*/}}{{- define "backend.labels" -}}
app.kubernetes.io/name: backend
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: backend
app.kubernetes.io/part-of: account
app.kubernetes.io/managed-by: Helm
{{- end }}
{{/*
telemetry有効時にだけendpointとDownward APIのnamespace/Pod UIDを渡す。
Source versionはimage build時にstampされ、環境の旧固定値で上書きしない。
Secret実値の変更は既存process環境へ反映されないためPod置換が必要。
*/}}{{- define "backend.env" -}}
- {name: PGHOST, value: {{ .Values.config.dbHost | quote }}}
- {name: PGPORT, value: {{ .Values.config.dbPort | quote }}}
- {name: PGDATABASE, value: {{ .Values.config.dbName | quote }}}
- {name: PGSSLMODE, value: {{ .Values.config.sslmode | quote }}}
- {name: LOG_LEVEL, value: {{ .Values.config.logLevel | quote }}}
{{- if .Values.telemetry.enabled }}
- {name: OTEL_EXPORTER_OTLP_ENDPOINT, value: {{ .Values.telemetry.endpoint | quote }}}
- {name: DEPLOYMENT_ENVIRONMENT, value: {{ .Values.telemetry.environment | quote }}}
- name: POD_NAMESPACE
  valueFrom: {fieldRef: {fieldPath: metadata.namespace}}
- name: POD_UID
  valueFrom: {fieldRef: {fieldPath: metadata.uid}}
{{- end }}
- name: PGUSER
  valueFrom: {secretKeyRef: {name: {{ .Values.secrets.existingSecret }}, key: {{ .Values.secrets.usernameKey }}}}
- name: PGPASSWORD
  valueFrom: {secretKeyRef: {name: {{ .Values.secrets.existingSecret }}, key: {{ .Values.secrets.passwordKey }}}}
{{- end }}
