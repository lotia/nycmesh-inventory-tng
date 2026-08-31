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

{{/*
How often the kubelet asks a backend pod whether it is ready.

Defined rather than written into the probe, because one other thing has to
agree with it: the ceiling on how long a connect to the database may block.
Both read this, so they cannot drift.
*/}}
{{- define "inventory-tng.readinessPeriodSeconds" -}}10{{- end -}}

{{/*
Refuses a release whose connect timeout is not shorter than that period.

Why that is the rule is the message below, and the arithmetic behind it is
docs/deployment.md#health-checks. Why it is refused HERE is that a values file
is where an operator raises the number, and nothing looked: the release
rendered, installed, and reproduced the fault the setting was added to
prevent, with every check green.
*/}}
{{- define "inventory-tng.connectTimeoutIsShortEnough" -}}
{{- $period := int (include "inventory-tng.readinessPeriodSeconds" .) -}}
{{- $bound := int .Values.django.databaseConnectTimeoutSeconds -}}
{{- if ge $bound $period -}}
{{- fail (printf "django.databaseConnectTimeoutSeconds is %d, which is not less than the readiness probe's periodSeconds (%d): a probe blocked on the database would still hold a worker when the next one arrives, and they accumulate until the pod serves nothing. Lower it, or shorten that period and raise the worker count together. See docs/deployment.md#health-checks." $bound $period) -}}
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
- name: DJANGO_LOG_LEVEL
  value: {{ .Values.django.logLevel | quote }}
- name: DJANGO_LOG_LEVELS
  value: {{ .Values.django.logLevels | quote }}
- name: DJANGO_LOG_FORMAT
  value: {{ .Values.django.logFormat | quote }}
- name: DJANGO_SECURITY_LOG_RATE
  value: {{ .Values.django.securityLogRate | quote }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .Values.django.otlpEndpoint | quote }}
- name: OTEL_SERVICE_NAME
  value: {{ .Values.django.otelServiceName | quote }}
- name: OTEL_RESOURCE_ATTRIBUTES
  value: {{ .Values.django.otelResourceAttributes | quote }}
- name: OTEL_TRACES_SAMPLER
  value: {{ .Values.django.tracesSampler | quote }}
- name: OTEL_TRACES_SAMPLER_ARG
  value: {{ .Values.django.tracesSamplerArg | quote }}
- name: TELEMETRY_PERSONAL_DATA
  value: {{ .Values.django.personalData | quote }}
- name: DEBUG_TRACE_LIFETIME_SECONDS
  value: {{ .Values.django.debugTraceLifetimeSeconds | quote }}
- name: DEBUG_TRACE_RATE
  value: {{ .Values.django.debugTraceRate | quote }}
- name: CSRF_TRUSTED_ORIGINS
  value: {{ .Values.django.csrfTrustedOrigins | quote }}
- name: NUM_PROXIES
  value: {{ .Values.django.numProxies | quote }}
- name: TRUSTED_PROXIES
  value: {{ .Values.django.trustedProxies | quote }}
- name: APPEND_BURST_RATE
  value: {{ .Values.django.appendBurstRate | quote }}
- name: APPEND_SUSTAINED_RATE
  value: {{ .Values.django.appendSustainedRate | quote }}
- name: CLIENT_REPORT_RATE
  value: {{ .Values.django.clientReportRate | quote }}
- name: DEVICE_ENROLMENT_RATE
  value: {{ .Values.django.deviceEnrolmentRate | quote }}
- name: REAUTHENTICATION_TIMEOUT_SECONDS
  value: {{ .Values.django.reauthenticationTimeoutSeconds | quote }}
- name: REQUIRE_SECOND_FACTOR
  value: {{ .Values.django.requireSecondFactor | quote }}
- name: PUBLIC_VOLUNTEER_DETAILS
  value: {{ .Values.django.publicVolunteerDetails | quote }}
- name: LABEL_BASE_URL
  value: {{ .Values.django.labelBaseUrl | quote }}
- name: DATABASE_CONNECT_TIMEOUT_SECONDS
  value: {{ .Values.django.databaseConnectTimeoutSeconds | quote }}
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

{{/*
How a pod authenticates to a private registry.

Rendered on all three pod specs, both Deployments and the migrate Job. Leaving
the Job out would be the failure shape migrate-job.yaml describes over its own
resources: not an error, a release that waits.

Empty by default, and that is the ordinary case: the images this repository
publishes are public, so nothing has to be supplied to pull them. It is for a
deployment that mirrors them somewhere of its own, or makes the package
private. The Secret is the operator's to create -- docs/deployment.md says how
and why this chart does not create it.
*/}}
{{- define "inventory-tng.imagePullSecrets" -}}
{{- with .Values.image.pullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}
