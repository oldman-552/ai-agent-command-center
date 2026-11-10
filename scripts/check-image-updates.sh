#!/usr/bin/env bash
# Lists OS security updates that are published but missing in built images (SEC-08 complement to Trivy).
# Works without a vulnerability database: it asks each image's own package manager (apt or apk).
#   scripts/check-image-updates.sh [image ...]      default: the platform's images
#   exit 1 if any image has pending security updates (or could not be checked with --strict)
set -uo pipefail
STRICT=0
[[ "${1:-}" == "--strict" ]] && { STRICT=1; shift; }
IMAGES=("$@")
if [[ ${#IMAGES[@]} -eq 0 ]]; then
  mapfile -t IMAGES < <(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(acc/|ai-agent-command-center)' | grep -v '<none>' | sort -u)
fi
proxy=()
[[ -n "${HTTPS_PROXY:-${https_proxy:-}}" ]] && proxy=(-e "https_proxy=${HTTPS_PROXY:-$https_proxy}" -e "http_proxy=${HTTP_PROXY:-${http_proxy:-}}")
fail=0
for img in "${IMAGES[@]}"; do
  out=$(docker run --rm --user 0 --network host "${proxy[@]}" --entrypoint sh "$img" -c '
    if command -v apt-get >/dev/null; then
      # the distribution security suite is always checked, even if the image does not list it (throwaway container)
      . /etc/os-release
      case "$ID" in
        ubuntu) echo "deb http://security.ubuntu.com/ubuntu ${VERSION_CODENAME}-security main universe" > /etc/apt/sources.list.d/zz-check-security.list ;;
        debian) echo "deb http://deb.debian.org/debian-security ${VERSION_CODENAME}-security main" > /etc/apt/sources.list.d/zz-check-security.list ;;
      esac
      apt-get update -qq >/dev/null 2>&1 || { echo "ERROR apt-get update failed"; exit 0; }
      apt-get -s upgrade 2>/dev/null | grep "^Inst" | grep -ci security; apt-get -s upgrade 2>/dev/null | grep "^Inst" | grep -i security | head -10
    elif command -v apk >/dev/null; then
      apk update -q >/dev/null 2>&1 || { echo "ERROR apk update failed"; exit 0; }
      apk -u list 2>/dev/null | wc -l; apk -u list 2>/dev/null | head -10
    else echo "ERROR no apt or apk"; fi' 2>&1)
  first=$(head -1 <<<"$out")
  if [[ "$first" == ERROR* || ! "$first" =~ ^[0-9]+$ ]]; then
    echo "?  $img: could not check (${first})"; [[ $STRICT == 1 ]] && fail=1
  elif [[ "$first" == 0 ]]; then
    echo "OK $img: no pending security updates"
  else
    echo "!! $img: $first pending security updates"; tail -n +2 <<<"$out" | sed 's/^/     /'; fail=1
  fi
done
exit $fail
