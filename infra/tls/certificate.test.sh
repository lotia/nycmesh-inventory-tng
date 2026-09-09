#!/usr/bin/env bash
# What the certificate generator must decide, and when it must decide again.
#
# The generator runs unattended, before nginx starts, on a machine nobody is
# watching. Every case here is one where getting it wrong produces a stack that
# comes up looking healthy and is unreachable from the one device the camera
# feature exists for -- which is the failure decision 0028 is about, arriving
# by a different road.
#
# THE CASE THIS SUITE EXISTS FOR is `an address that has changed`. The first
# version of the generator reused a certificate on the strength of the files
# existing, so a laptop that moved network served a certificate for an address
# nobody was dialling. A name mismatch is the one warning a browser will not
# let you click past, so the app was unreachable and said nothing useful. It
# was found by hand; nothing would have found it again.
#
# Usage: certificate.test.sh
#
# Drives infra/tls/certificate.sh itself, pointed at a temporary directory
# through CERTIFICATE_DIR. Needs `openssl` on PATH, which is what the image
# installs and what a CI runner already has.

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/../../scripts/testlib.sh"

workspace

GENERATOR="$HERE/certificate.sh"
CERTIFICATES="$WORK/certificate"

# generate <host>: run the real generator for that address.
generate() {
  mkdir -p "$CERTIFICATES"
  CERTIFICATE_DIR="$CERTIFICATES" TLS_HOST="$1" sh "$GENERATOR" 2>&1
}

# covers <host>: whether what is on disk now answers for that address.
#
# READS THE VERDICT, NOT THE EXIT STATUS, for the reason certificate.sh gives
# at length: `openssl x509` exits 0 whether -checkip/-checkhost matched or not
# before 3.2. A helper here that read the status would agree with a correct
# generator on a new openssl and agree with a BROKEN one on an old one, which
# is the same trap in the file whose job is to catch it -- and it would be
# silent, because a suite whose negative case cannot fail still counts as
# passing. Both spellings are asked and the answer is whichever replied, so
# this needs no copy of the generator's own classification: that rule is held
# instead by the SAN assertions below, which are what actually pin it.
# Answers `yes` or `no` rather than a status, because that is what `assert`
# reads -- and the two words share no substring, so a case cannot pass by
# matching part of the other answer.
# NOT PIPED INTO `grep -q`, which is how this was first written and which was
# wrong under this suite's own `set -o pipefail`: -q exits on the first match,
# the second openssl is then killed by SIGPIPE, and pipefail reports the
# pipeline as failed however well it matched. Captured and matched in the
# shell instead, which is also what certificate.sh does.
covers() {
  local verdict
  verdict=$(
    openssl x509 -in "$CERTIFICATES/tls.crt" -noout -checkip "$1" 2>/dev/null
    openssl x509 -in "$CERTIFICATES/tls.crt" -noout -checkhost "$1" 2>/dev/null
  )
  case $verdict in
    *"does match"*) echo yes ;;
    *) echo no ;;
  esac
}

sans() { openssl x509 -in "$CERTIFICATES/tls.crt" -noout -ext subjectAltName; }

fresh() { rm -rf "$CERTIFICATES"; }

echo "making one at all"

fresh
out=$(generate 10.0.0.5)
assert "$out" $? 0 "making a self-signed certificate" "an empty directory gets a certificate"
assert "$(covers 10.0.0.5)" 0 0 "yes" "and it answers for the address it was given"
assert "$(covers 10.0.0.9)" 0 0 "no" "and not for one it was never given, which is what makes the case above mean something"
assert "$(sans)" 0 0 "IP Address:10.0.0.5" "which is carried as a subject alternative name"
assert "$(sans)" 0 0 "DNS:localhost" "beside localhost, so the developer's own browser is served too"
assert "$(sans)" 0 0 "IP Address:127.0.0.1" "and the loopback address"

out=$(CERTIFICATE_DIR="$CERTIFICATES" sh "$GENERATOR" 2>&1)
status=$?
# ANY REFUSAL, NOT A PARTICULAR NUMBER. A shell that meets `${VAR:?}` picks its
# own exit status -- bash says 1, dash and busybox ash say 2 -- and `sh` here is
# whichever one the machine has. Pinning the number pins the suite to the
# machine it was written on: it passed where /bin/sh is bash and went red on the
# runner, which is the worst way round for a guard nobody watches.
[ "$status" -eq 0 ] || status=1
assert "$out" "$status" 1 "TLS_HOST" "no address at all is refused, naming the variable"

echo
echo "deciding whether to make another"

fresh
generate 10.0.0.5 >/dev/null
out=$(generate 10.0.0.5)
assert "$out" $? 0 "reusing the certificate" "the same address reuses what is there"
refute "$out" 0 0 "making a self-signed" "and does not make a second one"

# The one this suite is for.
out=$(generate 10.0.0.9)
assert "$out" $? 0 "does not cover 10.0.0.9" "an address that has changed is noticed"
assert "$out" 0 0 "making a self-signed certificate" "and earns a new certificate"
assert "$(covers 10.0.0.9)" 0 0 "yes" "which answers for the new address"
assert "$(covers 10.0.0.5)" 0 0 "no" "and has stopped answering for the old one"
assert "$out" 0 0 "will ask again" "and says the trust decision has to be made again"

# certificate.sh classifies TLS_HOST in three branches and this is the one no
# case reached: digits and dots that are not an address. It must not become an
# IP subject alternative name, which openssl would refuse outright.
fresh
out=$(generate 192.168.1)
assert "$out" $? 0 "making a self-signed certificate" "a digit-and-dot string that is not an address still works"
assert "$(sans)" 0 0 "DNS:192.168.1" "and is carried as a name rather than as an address"

fresh
generate 10.0.0.5 >/dev/null
out=$(generate laptop.local)
assert "$out" $? 0 "making a self-signed certificate" "so does moving from an address to a name"
assert "$(sans)" 0 0 "DNS:laptop.local" "and a name is a DNS entry rather than an IP one"

echo
echo "a certificate that is running out"

fresh
mkdir -p "$CERTIFICATES"
# Built here rather than by the generator, because a generator that could be
# asked for an expiring certificate would be a generator with a knob whose only
# caller is this line.
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -keyout "$CERTIFICATES/tls.key" -out "$CERTIFICATES/tls.crt" \
  -subj "/CN=10.0.0.5" -addext "subjectAltName=IP:10.0.0.5" >/dev/null 2>&1

out=$(generate 10.0.0.5)
assert "$out" $? 0 "making a self-signed certificate" "one expiring inside the week is replaced"
assert "$(openssl x509 -in "$CERTIFICATES/tls.crt" -noout -checkend 604800)" $? 0 "not expire" \
  "and what replaces it has more than a week left"

echo
echo "what a half-finished run leaves behind"

fresh
generate 10.0.0.5 >/dev/null
before=$(cat "$CERTIFICATES/tls.crt")
# A key with no certificate is what nginx refuses to start on, so the generator
# writes beside the pair and moves into place. Leftovers from an interrupted
# run must therefore not be mistaken for the real thing.
: >"$CERTIFICATES/tls.crt.new"
: >"$CERTIFICATES/tls.key.new"
out=$(generate 10.0.0.5)
assert "$out" $? 0 "reusing the certificate" "leftovers from an interrupted run are not the pair"
assert "$(cat "$CERTIFICATES/tls.crt")" 0 0 "$before" "and the working certificate is untouched"

# The other half of the same interruption, and the one that cannot be fixed by
# restarting. `key_matches_certificate` carries why the window exists and what
# nginx does when it is landed in; what this case pins is the scene it leaves
# behind -- a key and a certificate that both cover the address and both have
# years left, so every other question here answers yes about them.
fresh
generate 10.0.0.5 >/dev/null
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout "$CERTIFICATES/tls.key" -out "$WORK/somebody-elses.crt" \
  -subj "/CN=10.0.0.5" -addext "subjectAltName=IP:10.0.0.5" >/dev/null 2>&1
out=$(generate 10.0.0.5)
assert "$out" $? 0 "does not match its key" "a key that is not the certificate's earns a new pair"
assert "$out" 0 0 "making a self-signed certificate" "rather than a stack nginx will not start"

verdict
