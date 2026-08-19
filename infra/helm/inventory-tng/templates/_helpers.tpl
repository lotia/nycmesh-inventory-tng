{{- define "inventory-tng.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "inventory-tng.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "inventory-tng.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "inventory-tng.labels" -}}
app.kubernetes.io/name: {{ include "inventory-tng.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "inventory-tng.backendImage" -}}
{{- printf "%s/%s-backend:%s" .Values.image.registry .Values.image.repository (default .Chart.AppVersion .Values.image.tag) -}}
{{- end -}}

{{- define "inventory-tng.frontendImage" -}}
{{- printf "%s/%s-frontend:%s" .Values.image.registry .Values.image.repository (default .Chart.AppVersion .Values.image.tag) -}}
{{- end -}}

{{/* Environment shared by the backend Deployment and the migration Job. */}}
{{- define "inventory-tng.backendEnv" -}}
- name: DJANGO_DEBUG
  value: {{ .Values.django.debug | quote }}
- name: DJANGO_ALLOWED_HOSTS
  value: {{ .Values.django.allowedHosts | quote }}
- name: CORS_ALLOWED_ORIGINS
  value: {{ .Values.django.corsAllowedOrigins | quote }}
- name: NUM_PROXIES
  value: {{ .Values.django.numProxies | quote }}
- name: APPEND_BURST_RATE
  value: {{ .Values.django.appendBurstRate | quote }}
- name: APPEND_SUSTAINED_RATE
  value: {{ .Values.django.appendSustainedRate | quote }}
- name: REAUTHENTICATION_TIMEOUT_SECONDS
  value: {{ .Values.django.reauthenticationTimeoutSeconds | quote }}
- name: LABEL_BASE_URL
  value: {{ .Values.django.labelBaseUrl | quote }}
- name: DJANGO_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.django.existingSecret }}
      key: DJANGO_SECRET_KEY
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.django.existingSecret }}
      key: DATABASE_URL
{{- end -}}

{{/*
Sign-in provider credentials, from an optional Secret.

envFrom rather than a named list: which providers a deployment offers is a
property of what is in that Secret (docs/decisions/0013-administrator-sign-in.md
point 1), so adding one should not need a chart change. `optional: true` is
what makes a deployment with no providers configured -- the ordinary case --
start rather than wait forever for a Secret nobody meant to create.
*/}}
{{- define "inventory-tng.backendEnvFrom" -}}
- secretRef:
    name: {{ .Values.django.providerSecret }}
    optional: true
{{- end -}}
