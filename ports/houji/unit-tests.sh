#!/usr/bin/env bash
# Run every Houji Python and shell unit test that needs no network or phone.
#
#   ports/houji/unit-tests.sh
#
# Tests are discovered, so a new tests/houji-*-test.{py,sh} or
# ports/houji/**/test-*.py is picked up without editing this script. All of
# them run even after a failure, and the exit status says whether any failed.
# The C and C++ tests are built from patched upstream sources and run by
# native-tests.sh instead.
set -uo pipefail

port=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$port/../.." && pwd)
cd "$root" || exit 1
export PYTHONDONTWRITEBYTECODE=1

tests=()
while IFS= read -r file; do tests+=("$file"); done < <(
    {
        # houji-ssc-close-test.py needs a patched iio-sensor-proxy tree; see native-tests.sh.
        find tests -maxdepth 1 -name 'houji-*-test.py' ! -name 'houji-ssc-close-test.py'
        find tests -maxdepth 1 -name 'houji-*-test.sh'
        # test-wmi-dbs-parser.py needs the pinned kernel archive; check-patches.py kernel runs it.
        find ports/houji -name 'test-*.py' -not -path '*/__pycache__/*' ! -name 'test-wmi-dbs-parser.py'
    } | LC_ALL=C sort -u
)

failed=()
for test in "${tests[@]}"; do
    printf '\n== %s\n' "$test"
    case $test in
        *.sh) command=(bash "$test") ;;
        *)    command=(python3 "$test") ;;
    esac
    if "${command[@]}"; then
        :
    else
        failed+=("$test")
    fi
done

printf '\n%d tests run, %d failed\n' "${#tests[@]}" "${#failed[@]}"
if ((${#failed[@]})); then
    printf 'FAILED: %s\n' "${failed[@]}"
    exit 1
fi
