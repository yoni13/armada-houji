#!/usr/bin/env bash
# Rebuild ../prebuilt/system/framework/armada-autofillstub.jar from AutofillStub.java with the same pinned
# Android platform and R8 as the clipboard stub in ../build.sh, then refresh its line in ../prebuilt.sha256.
# Run it after changing AutofillStub.java; the committed jar is what the image ships.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
lepton="$(cd -- "${here}/.." && pwd)"
# shellcheck source=../BASE.env
source "${lepton}/BASE.env"

work="$(mktemp -d)"
trap 'rm -rf -- "${work}"' EXIT
cd "${work}"

curl --fail --silent --show-error --location --retry 3 --remote-name \
    "https://dl.google.com/android/repository/${ANDROID_PLATFORM_ZIP}"
curl --fail --silent --show-error --location --retry 3 -o r8.jar \
    "https://dl.google.com/android/maven2/com/android/tools/r8/${R8_VERSION}/r8-${R8_VERSION}.jar"
sha256sum --check --strict <<EOF
${ANDROID_PLATFORM_SHA256}  ${ANDROID_PLATFORM_ZIP}
${R8_SHA256}  r8.jar
EOF
unzip -q -j "${ANDROID_PLATFORM_ZIP}" '*/android.jar'
javac --release 11 -cp android.jar -d classes "${here}/AutofillStub.java"
java -cp r8.jar com.android.tools.r8.D8 --release --min-api "${ANDROID_API}" --lib android.jar \
    --output armada-autofillstub.jar classes/*.class

jar_path=system/framework/armada-autofillstub.jar
install -Dm0644 armada-autofillstub.jar "${lepton}/prebuilt/${jar_path}"
digest="$(sha256sum armada-autofillstub.jar | cut -d' ' -f1)"
sums="${lepton}/prebuilt.sha256"
grep -v "  ${jar_path}\$" "${sums}" >"${work}/sums" || true
printf '%s  %s\n' "${digest}" "${jar_path}" >>"${work}/sums"
LC_ALL=C sort -k2 "${work}/sums" >"${sums}"
echo "${jar_path} ${digest}"
