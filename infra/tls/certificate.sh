#!/bin/sh
# Make a certificate for this stack, before nginx starts, and make a new one
# whenever the old one has stopped being true.
#
# Runs from the base image's entrypoint directory, so it happens on every start
# and finishes before anything listens.
#
# THE ONE THING THIS FILE EXISTS TO PREVENT is a contributor typing an openssl
# command. That is decision 0028's acceptance criterion, stated there in the
# owner's own words. So the command lives here, runs in a container, and
# appears in no document.
set -eu

# Where the pair lives. The default is the volume the image declares and is
# what runs in every container; the variable exists so that
# `certificate.test.sh` can drive this file itself against a temporary
# directory rather than assert things about a copy of it.
#
# THAT IS THE DIFFERENCE BETWEEN A TESTED SCRIPT AND A TESTED PARAPHRASE. A
# suite that reimplemented the reuse rule in order to check it would go green
# on its own reasoning while this file rotted, which is the failure mode the
# rule here was already found to have once.
CERTIFICATE_DIR=${CERTIFICATE_DIR:-/certificate}

CERTIFICATE="$CERTIFICATE_DIR/tls.crt"
KEY="$CERTIFICATE_DIR/tls.key"

# The address a phone will dial, which the container cannot work out for
# itself: it sees the container network and not the LAN the phone is on.
# compose.yaml says why there is no default.
: "${TLS_HOST:?tls: TLS_HOST must be the address a phone will dial, e.g. 10.0.0.5 -- see DEVELOPERS.md}"

# An address is an IP subject alternative name and a name is a DNS one. A
# browser checks the matching kind and nothing else, so getting this wrong
# produces a certificate that looks right in every listing and is refused by
# every client. Decided once, here, and used by both the check and the request
# below -- two copies of this test drifting apart is the kind of bug that only
# shows up on somebody else's network.
case $TLS_HOST in
    *[!0-9.]*) KIND=host ;;
    *.*.*.*)   KIND=ip ;;
    *)         KIND=host ;;
esac

# Whether the certificate on the volume still covers the address being asked
# for, asked of openssl rather than by reading the SANs ourselves. It knows the
# matching rules -- wildcards, IP forms, the difference between the two kinds
# above -- and we do not, which is also AGENTS.md rule 3's whole posture:
# established tool, own interface, no re-implementation.
#
# READ OUT OF THE VERDICT IT PRINTS, NOT OUT OF ITS EXIT STATUS, which is the
# one thing about this interface that cannot be taken on trust. `openssl x509`
# exits 0 whether `-checkip`/`-checkhost` matched or not on OpenSSL 3.0 and
# earlier; only 3.2 began reporting the mismatch in the status. Ubuntu 24.04
# ships 3.0, so a check written on the status fails OPEN on a CI runner and on
# most contributors' machines while passing on whichever machine happens to
# carry a newer one -- which is this file's own bug, arriving through the very
# call written to prevent it. The printed line is stable across every version:
# `does match`, or `does NOT match`, which does not contain it.
#
# AND THE OBVIOUS SAVING HERE IS A TRAP, measured rather than assumed: folding
# this into the -checkend call below prints only the expiry line and drops the
# match verdict entirely, so the check would go on passing while testing
# nothing. Three openssl calls in the reuse path cost tens of milliseconds
# before nginx starts; leave them apart.
covers_the_address() {
    verdict=$(
        if [ "$KIND" = ip ]; then
            openssl x509 -in "$CERTIFICATE" -noout -checkip "$TLS_HOST" 2>/dev/null
        else
            openssl x509 -in "$CERTIFICATE" -noout -checkhost "$TLS_HOST" 2>/dev/null
        fi
    )
    case $verdict in
        *"does match"*) return 0 ;;
        *) return 1 ;;
    esac
}

# A week's grace, so a certificate cannot expire in the middle of an afternoon
# that started fine.
still_valid_for_a_while() {
    openssl x509 -in "$CERTIFICATE" -noout -checkend 604800 >/dev/null 2>&1
}

# THE TWO FILES BELONG TO EACH OTHER. Two files cannot be moved into place in
# one step, so a run killed between the moves below leaves the new key beside
# the old certificate -- both present, both valid, and not a pair. Every other
# question here would answer yes about them, so the next start would reuse them
# and nginx would refuse to start with `key values mismatch` on every start
# after that, which is a stack that cannot be fixed by restarting it.
#
# Asked by comparing the public key each file carries, which is openssl
# answering rather than us: the certificate's own, and the one derived from the
# private key.
key_matches_certificate() {
    [ "$(openssl x509 -in "$CERTIFICATE" -noout -pubkey 2>/dev/null)" \
        = "$(openssl pkey -in "$KEY" -pubout 2>/dev/null)" ]
}

# WHY THIS IS NOT MERELY `[ -f ... ]`, which is what it was first and which was
# wrong in the way that matters. A laptop's LAN address changes -- a different
# network, a DHCP lease that moved -- and the old certificate is still sitting
# on the volume, perfectly valid, for an address nobody is dialling any more.
# Reusing it on the strength of its existence serves a certificate whose name
# does not match the address in the bar, and THAT warning is the one a browser
# will not let you click past. So the stack would come up looking healthy and be
# unreachable from the one device it exists for, saying nothing useful.
if [ -f "$CERTIFICATE" ] && [ -f "$KEY" ]; then
    if covers_the_address && still_valid_for_a_while && key_matches_certificate; then
        echo "tls: reusing the certificate in /certificate, which covers $TLS_HOST"
        exit 0
    fi
    # Said out loud, because the consequence lands on a person: a new
    # certificate is a new trust decision on every device that had accepted the
    # old one.
    echo "tls: the certificate in /certificate does not cover $TLS_HOST, is expiring, or does not match its key; making a new one"
    echo "tls: devices that trusted the old certificate will ask again"
fi

if [ "$KIND" = ip ]; then
    NAMES="DNS:localhost,IP:127.0.0.1,IP:$TLS_HOST"
else
    NAMES="DNS:localhost,IP:127.0.0.1,DNS:$TLS_HOST"
fi

echo "tls: making a self-signed certificate for $NAMES"

# Self-signed, which is decision 0028 as amended: the browser warning is
# expected and is documented where a contributor meets it, rather than avoided
# by a per-device trust ritual nobody asked for.
#
# -nodes because nginx starts unattended, and a passphrase it cannot be given
# is a stack that hangs rather than serves. The key never leaves this volume,
# is never committed, and is worth exactly what a laptop's own certificate is.
#
# Written to temporary names and moved into place, so that a run interrupted
# before they are written leaves the previous pair rather than a key with no
# certificate -- which nginx refuses to start on, turning a stopped generator
# into a stopped stack. Two moves are not one step, so the window where the new
# key sits beside the old certificate is closed by `key_matches_certificate`
# above rather than by the order of these lines, which cannot close it.
openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$KEY.new" \
    -out "$CERTIFICATE.new" \
    -days 825 \
    -subj "/CN=$TLS_HOST" \
    -addext "subjectAltName=$NAMES" \
    -addext "basicConstraints=critical,CA:FALSE" \
    -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
    -addext "extendedKeyUsage=serverAuth"

chmod 600 "$KEY.new"
mv "$KEY.new" "$KEY"
mv "$CERTIFICATE.new" "$CERTIFICATE"

echo "tls: certificate written; the browser warning it produces is expected"
