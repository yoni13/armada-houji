#!/usr/bin/env bash
# Build autofill-test.apk, a one-screen app that reproduces the autofill crash, for checking AutofillStub on a
# real Lepton container (see README.md). Uses the same pinned Android platform as ../build.sh. Output: $1 or ./out.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
lepton="$(cd -- "${here}/../.." && pwd)"
# shellcheck source=../../BASE.env
source "${lepton}/BASE.env"
out="$(mkdir -p "${1:-${here}/out}" && cd -- "${1:-${here}/out}" && pwd)"

work="$(mktemp -d)"
trap 'rm -rf -- "${work}"' EXIT
cd "${work}"
curl --fail --silent --show-error --location --retry 3 --remote-name \
    "https://dl.google.com/android/repository/${ANDROID_PLATFORM_ZIP}"
echo "${ANDROID_PLATFORM_SHA256}  ${ANDROID_PLATFORM_ZIP}" | sha256sum --check --strict
unzip -q -j "${ANDROID_PLATFORM_ZIP}" '*/android.jar'

javac --release 11 -cp android.jar -d classes "${here}"/src/org/armada/autofilltest/*.java
mkdir dex
d8 --release --min-api 26 --lib android.jar --output dex classes/org/armada/autofilltest/*.class
aapt2 link -o unsigned.apk --manifest "${here}/AndroidManifest.xml" -I android.jar \
    --min-sdk-version 26 --target-sdk-version 30
python3 - <<'PY'
import zipfile
with zipfile.ZipFile('unsigned.apk', 'a') as apk:
    apk.write('dex/classes.dex', 'classes.dex')
PY
zipalign -f -p 4 unsigned.apk aligned.apk
keytool -genkeypair -keystore test.keystore -storepass armada -keypass armada -alias test -keyalg RSA \
    -keysize 2048 -validity 3650 -dname 'CN=Armada autofill test' >/dev/null 2>&1
apksigner sign --ks test.keystore --ks-pass pass:armada --key-pass pass:armada --out "${out}/autofill-test.apk" aligned.apk
echo "${out}/autofill-test.apk"
