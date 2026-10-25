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

{{/*
Deployment と DB 移行 Job が同じリリースを使っていることを確認するための注釈。
形式ごとの項目をここにまとめ、片方だけ更新されることを防ぐ。
GitHub と旧 GitLab の形式は、混ぜずにそれぞれの項目を出力する。
*/}}
{{- define "backend.releaseAnnotations" -}}
account.lab/image-digest: {{ .Values.image.digest | quote }}
{{- if eq (default "0" .Values.release.schemaVersion) "github-v1" }}
account.lab/release-schema-version: "github-v1"
account.lab/source-repository: {{ .Values.release.sourceRepository | quote }}
account.lab/source-commit: {{ .Values.release.sourceCommit | quote }}
account.lab/github-run-id: {{ .Values.release.runId | quote }}
account.lab/github-run-attempt: {{ .Values.release.runAttempt | quote }}
account.lab/release-record-url: {{ .Values.release.recordUrl | quote }}
account.lab/release-record-sha256: {{ .Values.release.recordSha256 | quote }}
account.lab/sbom-url: {{ .Values.release.sbomUrl | quote }}
account.lab/sbom-sha256: {{ .Values.release.sbomSha256 | quote }}
{{- else }}
account.lab/source-project-id: {{ .Values.release.sourceProjectId | quote }}
account.lab/source-commit: {{ .Values.release.sourceCommit | quote }}
{{- if eq (default "0" .Values.release.schemaVersion) "2" }}
account.lab/release-schema-version: "2"
account.lab/build-pipeline-id: {{ .Values.release.buildPipelineId | quote }}
account.lab/build-job-id: {{ .Values.release.buildJobId | quote }}
account.lab/scan-pipeline-id: {{ .Values.release.scanPipelineId | quote }}
account.lab/scan-job-id: {{ .Values.release.scanJobId | quote }}
account.lab/release-record-url: {{ .Values.release.recordUrl | quote }}
account.lab/release-record-sha256: {{ .Values.release.recordSha256 | quote }}
account.lab/sbom-url: {{ .Values.release.sbomUrl | quote }}
account.lab/sbom-sha256: {{ .Values.release.sbomSha256 | quote }}
{{- else }}
account.lab/pipeline-id: {{ .Values.release.pipelineId | quote }}
account.lab/pipeline-url: {{ .Values.release.pipelineUrl | quote }}
{{- end }}
{{- end }}
{{- end }}
