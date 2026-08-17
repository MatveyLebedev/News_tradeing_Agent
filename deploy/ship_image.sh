#!/usr/bin/env bash
# Ship a locally built image straight to the server over SSH — no registry, nothing published.
#
# Why no registry: on 2026-08-11 the public Docker Hub image `matveylebedev891/telegram-bot`
# was found to contain a real `/app/.env`. Its OpenRouter key (sha256 f502213f95f9…) was the
# SAME key production was using, and OpenRouter's own 403 named that key id. The image had
# ~180 pulls. `docker push` had created the repo PUBLIC by default, silently.
#
# The replacement removes third parties from the delivery path entirely: the image travels
# through the SSH channel that already exists for ops. Slower than pushing layers (the whole
# image moves every time), which is affordable at a weekly deploy cadence.
#
# Usage:
#   deploy/ship_image.sh <image:tag> [user@host]
#   DEPLOY_HOST=root@1.2.3.4 deploy/ship_image.sh longterm-agent:0.11.0
#
# The SSH password is prompted by ssh itself — this script never sees, stores or logs it.

set -euo pipefail

IMAGE="${1:-}"
TARGET="${2:-${DEPLOY_HOST:-}}"

if [[ -z "$IMAGE" || -z "$TARGET" ]]; then
    echo "usage: $0 <image:tag> [user@host]   (or set DEPLOY_HOST)" >&2
    exit 2
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "✗ no such local image: $IMAGE" >&2
    echo "  build it first, e.g.:" >&2
    echo "  docker build --platform linux/amd64 -t $IMAGE -f <dockerfile> ." >&2
    exit 1
fi

# --- gate: never ship an image carrying secrets ------------------------------------------
# This is the check whose absence caused the leak. It runs on the artifact itself, not on
# .dockerignore, so it holds even when someone forgets to update the ignore file — and it
# runs before anything leaves this machine.
echo "→ scanning $IMAGE for baked secrets…"
FOUND=$(docker run --rm --entrypoint sh "$IMAGE" -c '
    find / -xdev \( -name ".env" -o -name ".env.*" ! -name ".env.example" -o -name "*.bak" \) 2>/dev/null
' 2>/dev/null || true)

if [[ -n "$FOUND" ]]; then
    echo "✗ REFUSING TO SHIP — secret-bearing files inside the image:" >&2
    echo "$FOUND" | sed 's/^/    /' >&2
    echo "  Fix .dockerignore (or the COPY lines) and rebuild." >&2
    exit 1
fi
echo "  clean — no .env / .bak inside"

LOCAL_ID=$(docker image inspect "$IMAGE" --format '{{.Id}}')
SIZE=$(docker image inspect "$IMAGE" --format '{{.Size}}')

# One authenticated connection, reused by both the transfer and the verification, so the
# password is typed once instead of twice. Torn down on exit even if the transfer fails.
#
# Deliberately /tmp and not $TMPDIR: a Unix socket path is capped near 104 bytes, and
# macOS sets TMPDIR to a ~50-character per-user path that blows the limit once ssh appends
# its own %C hash ("path too long for Unix domain socket").
CTL="/tmp/ship-%C"
SSH=(ssh -o ControlMaster=auto -o ControlPath="$CTL" -o ControlPersist=60)
cleanup() { "${SSH[@]}" -O exit "$TARGET" 2>/dev/null || true; }
trap cleanup EXIT

echo "→ shipping $IMAGE ($(( SIZE / 1024 / 1024 )) MB uncompressed) to $TARGET"
echo "  ssh will ask for the password once; it is not handled by this script."

# Stream: never lands as a file on either side, so there is no archive to forget about.
if command -v pv >/dev/null 2>&1; then
    docker save "$IMAGE" | pv -s "$SIZE" | gzip -1 | "${SSH[@]}" "$TARGET" 'gunzip | docker load'
else
    docker save "$IMAGE" | gzip -1 | "${SSH[@]}" "$TARGET" 'gunzip | docker load'
fi

# --- verify the server got the same bytes ------------------------------------------------
REMOTE_ID=$("${SSH[@]}" "$TARGET" "docker image inspect '$IMAGE' --format '{{.Id}}'" 2>/dev/null || true)
if [[ "$REMOTE_ID" != "$LOCAL_ID" ]]; then
    echo "✗ image id mismatch after load — local $LOCAL_ID, remote ${REMOTE_ID:-<absent>}" >&2
    exit 1
fi

echo "✓ $IMAGE on $TARGET, id matches local"
echo
echo "Now recreate the container(s). See README.md for the exact run commands —"
echo "they bind --env-file /root/trade_agent/.env; do not pass secrets as -e literals."
