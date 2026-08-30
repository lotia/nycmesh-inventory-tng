# Frontend image: Vite build output served as static files by nginx.
# There is no Node runtime in production -- see docs/decisions/0002-frontend-stack.md.

FROM docker.io/library/node:24-bookworm-slim AS builder

WORKDIR /app

# Dependencies install in their own layer so source edits do not re-run npm ci.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build


FROM docker.io/nginxinc/nginx-unprivileged:1-alpine AS runtime

# Runs as uid 101 (non-root) by default, so it satisfies a restricted
# PodSecurity policy without extra securityContext wrangling.

# Origin nginx forwards Django's paths to; which paths, and why, is in
# docs/architecture.md. Overridden per environment.
ENV BACKEND_ORIGIN=http://backend:8000

# Where a browser's spans are forwarded to. Defaulted to the discard port
# rather than left empty on purpose: `proxy_pass` needs an address, so an
# empty value is a configuration nginx refuses to start with -- and a
# deployment with no collector would then not serve the app at all. This
# address refuses the connection instead, which is a 502 on a path nothing
# posts to unless somebody has been handed a debug token. Set it to a real
# collector to turn the browser's half on; docs/observability.md.
ENV COLLECTOR_ORIGIN=http://127.0.0.1:9

# Asks the base image's entrypoint to read /etc/resolv.conf and publish the
# nameservers as NGINX_LOCAL_RESOLVERS, which nginx.conf.template needs in
# order to re-resolve its upstreams while running. Set here rather than in
# compose.yaml or the chart because the template does not render without it:
# every place this image runs needs it, so the image carries it.
#
# Read at start rather than written down, because the address differs by
# runtime -- Docker answers on 127.0.0.11 and rootless Podman on an address
# belonging to the network the container joined.
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# The base image renders /etc/nginx/templates/*.template through envsubst at
# startup, which is how BACKEND_ORIGIN gets substituted.

EXPOSE 8080
