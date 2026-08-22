{{- define "inventory-tng.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
The prefix every resource this chart makes is named with.

A release whose name already contains the chart's is not prefixed again, which
is the collapse the scaffold ships with and this chart was missing: the install
docs/deployment.md prints names the release `inventory-tng`, so without it
every resource rendered as `inventory-tng-inventory-tng-backend` and every
`kubectl` line in that page had to carry the stutter.
*/}}
{{- define "inventory-tng.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := include "inventory-tng.name" . -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Refuses a release whose ingress sends a hostname Django will not answer to.

The probes cannot catch this and no probe could: they reach the pod by its own
address, so they go green while every request through the ingress is refused —
nginx forwards the browser's Host untouched, Django answers 400, and the app
shell still loads because only /api is affected. Two pods Ready and a dead
site, with DisallowedHost invisible because DEBUG is off.

Matched the way Django matches, because a guard stricter than Django refuses
releases that work and nobody debugs a chart that says no to something
correct: `django.utils.http.is_same_domain` lowercases both sides and accepts
an exact name, `*`, a leading-dot pattern as a suffix, and a leading-dot
pattern against the bare apex. That rule is restated here because a template
cannot ask Django; inventory/tests/test_chart.py holds this against the real
function so the copy cannot drift.
*/}}
{{- define "inventory-tng.ingressHostIsAllowed" -}}
{{- $host := lower .Values.ingress.host -}}
{{- $covered := false -}}
{{- range splitList "," (.Values.django.allowedHosts | toString) -}}
{{- $pattern := lower (trim .) -}}
{{- if or (eq $pattern "*") (eq $pattern $host) (and (hasPrefix "." $pattern) (or (hasSuffix $pattern $host) (eq (trimPrefix "." $pattern) $host))) -}}
{{- $covered = true -}}
{{- end -}}
{{- end -}}
{{- if not $covered -}}
{{- fail (printf "ingress.host is %q, which django.allowedHosts (%q) does not cover: Django would refuse every request the ingress forwards, while the pods stayed Ready. Add it, or widen the list. See docs/deployment.md#health-checks." $host .Values.django.allowedHosts) -}}
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
# The addresses this pod answers to that nobody could have listed in advance:
# its own, which is what the kubelet asks for when it probes. Read from the pod
# because it does not exist until the pod does. The variable is a list and the
# downward API renders status.podIPs comma-separated into one, so a dual-stack
# cluster that needs both families is a one-word change here rather than a
# shape change in settings.py -- status.podIP until something needs that.
# What it costs to get this wrong is docs/deployment.md#health-checks.
- name: DJANGO_EXTRA_ALLOWED_HOSTS
  valueFrom:
    fieldRef:
      fieldPath: status.podIP
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
