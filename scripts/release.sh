#!/usr/bin/env bash
# Signs and pushes the release tag of one Odoo series (329 D14, T15). The tag is the manifest's version on the series
# branch as GitHub holds it, signed with the release key (SSH; its public half is .github/allowed_signers); the release
# workflow then checks the tag and puts the zip and its SHA-256 on the release. The private key is read for the
# signing only, into a directory of its own that is removed on exit.
#   DATLAY_RELEASE_KEY_REF='op://<vault>/<item>/private key' scripts/release.sh 17.0
set -euo pipefail
series="${1:?usage: scripts/release.sh <series, e.g. 17.0>}"
ref="${DATLAY_RELEASE_KEY_REF:?set it to the 1Password reference of the private key of the release key}"
[[ "$series" =~ ^[0-9]{2}\.0$ ]] || { echo "not a series: $series" >&2; exit 1; }

git fetch --quiet --tags origin "+refs/heads/$series:refs/remotes/origin/$series"
tag=$(git show "origin/$series:datlay_connector/__manifest__.py" | python3 -c "import ast, sys; print(ast.literal_eval(sys.stdin.read())['version'])")
[[ "$tag" == "$series."* ]] || { echo "the manifest on $series says $tag" >&2; exit 1; }
if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
  echo "$tag is already tagged: raise the manifest's version first" >&2; exit 1
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
(umask 077 && op read "$ref?ssh-format=openssh" > "$tmp/key")
git show "origin/$series:.github/allowed_signers" > "$tmp/allowed_signers"
git -c gpg.format=ssh -c user.signingkey="$tmp/key" tag -s "$tag" -m "datlay_connector $tag" "origin/$series"
git -c gpg.format=ssh -c gpg.ssh.allowedSignersFile="$tmp/allowed_signers" tag -v "$tag"
git push origin "refs/tags/$tag"
echo "pushed $tag: the release workflow checks it and builds its zip and SHA-256"
