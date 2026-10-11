{{- define "order.labels" -}}
app.kubernetes.io/name: order
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: order
app.kubernetes.io/part-of: order
app.kubernetes.io/managed-by: Helm
{{- end }}
{{- define "order.env" -}}
- {name: PGHOST, value: {{ .Values.config.dbHost | quote }}}
- {name: PGPORT, value: {{ .Values.config.dbPort | quote }}}
- {name: PGDATABASE, value: {{ .Values.config.dbName | quote }}}
- {name: PGSSLMODE, value: {{ .Values.config.sslmode | quote }}}
- {name: LOG_LEVEL, value: {{ .Values.config.logLevel | quote }}}
- name: PGUSER
  valueFrom: {secretKeyRef: {name: {{ .Values.secrets.existingSecret }}, key: {{ .Values.secrets.usernameKey }}}}
- name: PGPASSWORD
  valueFrom: {secretKeyRef: {name: {{ .Values.secrets.existingSecret }}, key: {{ .Values.secrets.passwordKey }}}}
{{- end }}

{{- define "order.releaseAnnotations" -}}
account.lab/image-digest: {{ .Values.image.digest | quote }}
account.lab/release-schema-version: {{ .Values.release.schemaVersion | quote }}
account.lab/service: order
account.lab/source-repository: {{ .Values.release.sourceRepository | quote }}
account.lab/source-commit: {{ .Values.release.sourceCommit | quote }}
account.lab/github-run-id: {{ .Values.release.runId | quote }}
account.lab/github-run-attempt: {{ .Values.release.runAttempt | quote }}
account.lab/release-record-url: {{ .Values.release.recordUrl | quote }}
account.lab/release-record-sha256: {{ .Values.release.recordSha256 | quote }}
account.lab/sbom-url: {{ .Values.release.sbomUrl | quote }}
account.lab/sbom-sha256: {{ .Values.release.sbomSha256 | quote }}
{{- end }}
