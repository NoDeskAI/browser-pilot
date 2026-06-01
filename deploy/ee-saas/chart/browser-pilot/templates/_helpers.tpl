{{- define "browser-pilot.name" -}}
browser-pilot
{{- end -}}

{{- define "browser-pilot.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "browser-pilot.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "browser-pilot.labels" -}}
app.kubernetes.io/name: {{ include "browser-pilot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "browser-pilot.image" -}}
{{- if not .Values.image.digest -}}
{{- fail "image.digest is required for EE SaaS deployments" -}}
{{- end -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- end -}}

{{- define "browser-pilot.backendServiceAccountName" -}}
{{- default "browser-pilot-backend" .Values.runtime.backendServiceAccountName -}}
{{- end -}}

{{- define "browser-pilot.sessionServiceAccountName" -}}
{{- default "browser-pilot-session" .Values.runtime.sessionServiceAccountName -}}
{{- end -}}
