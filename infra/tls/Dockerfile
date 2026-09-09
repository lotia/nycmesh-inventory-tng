# TLS termination for local development, and nothing else.
#
# WHY THIS IS ITS OWN IMAGE rather than a second port on the frontend:
# decision 0028 point 3, which carries the argument and is not repeated here.
# This image is never deployed -- a cluster terminates TLS at its ingress with
# a certificate from a real authority, and docs/deployment.md offers no switch
# to skip it.
#
# The same nginx as the frontend, so there is one proxy to understand rather
# than two.
FROM docker.io/nginxinc/nginx-unprivileged:1-alpine

# openssl generates the certificate at startup. It is the whole reason this
# image exists rather than the frontend's being reused, and it is what keeps
# AGENTS.md rule 3 satisfied: an established tool, driven through its own
# published interface, doing the one thing nobody here may hand-roll.
#
# The certificate directory is made and given to nginx's own uid HERE, in the
# image, because that is what decides the ownership of the named volume
# mounted over it. A volume mounted onto a path the image does not have is
# created owned by root, and this container never runs as root, so the
# generator below would find it unwritable and the stack would come up with no
# certificate and no useful complaint.
USER root
RUN apk add --no-cache openssl && install -d -o 101 -g 101 /certificate
USER 101

# Asks the base image's entrypoint to publish /etc/resolv.conf's nameservers
# as NGINX_LOCAL_RESOLVERS, which the template below needs in order to
# re-resolve its upstream while running. frontend/Dockerfile carries the same
# line and the argument for it; without this the template renders with the
# variable's own name in it and nginx refuses to start with `host not found in
# resolver "${NGINX_LOCAL_RESOLVERS}"`, which names the symptom and not this.
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1

# The base image runs every executable here before starting nginx, and renders
# every .template through envsubst. Both halves of this image are therefore
# ordinary uses of what the image already does, rather than an entrypoint of
# our own that has to remember to hand over.
COPY --chown=101:101 certificate.sh /docker-entrypoint.d/40-certificate.sh
COPY --chown=101:101 tls.conf.template /etc/nginx/templates/default.conf.template

# Where the certificate lives, so that a `compose down` without `-v` keeps it
# and a phone that has already been told to trust it is not asked again.
VOLUME /certificate

# Not 443. Rootless Podman cannot bind a privileged port without a sysctl
# change on the host, and asking every contributor to make one is the kind of
# step decision 0028 exists to remove. A port does not affect whether an origin
# is a secure context -- the browser cares about the scheme and the host -- so
# https://10.0.0.5:8443 is one and the camera works there.
EXPOSE 8443
