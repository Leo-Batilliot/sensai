#!/usr/bin/env bash
# Runs the checks of .github/workflows/ci.yml locally: lint and tests on every Python
# version of the CI matrix, each in a fresh virtual environment with freshly installed
# dependencies, like a GitHub runner. Stand-in while the GitHub Actions budget of the
# organization is exhausted (decision D-08 in docs/decisions.md).
#
# Needs uv, which also downloads a missing Python version. The environments live outside
# the repository (CI_DIR), so ruff and pytest never see them and the repository on /mnt/c
# is not slowed down.
#
#   make ci
#   CI_PYTHONS=3.12 make ci        # a single version, faster while iterating
set -euo pipefail

PYTHONS="${CI_PYTHONS:-3.10 3.12}" # same versions as the CI matrix
CI_DIR="${CI_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/sensai-ci}"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv est introuvable : installez-le (README, « Installation sous Linux », étape 3)." >&2
    exit 1
fi

cd "$(git rev-parse --show-toplevel)"
commit="$(git rev-parse --short HEAD)"
dirty=""
if ! git diff --quiet HEAD; then
    dirty=" (avec des modifications non commitées)"
fi

results=()
for version in $PYTHONS; do
    echo "=== Python $version"
    venv="$CI_DIR/py$version"
    rm -rf "$venv"
    uv venv --quiet --python "$version" "$venv"
    python="$venv/bin/python"
    [ -x "$python" ] || python="$venv/Scripts/python.exe" # Git Bash on Windows
    VIRTUAL_ENV="$venv" uv pip install --quiet -r requirements-dev.txt

    "$python" -m ruff check .
    "$python" -m ruff format --check .
    log="$venv/pytest.log"
    "$python" -m pytest -m "not ollama and not external" -q -p no:cacheprovider | tee "$log"
    tests="$(tail -n 1 "$log" | sed -E 's/ in [0-9.]+s.*$//')"
    results+=("- Python $version : ruff check OK, ruff format --check OK, pytest $tests")
done

echo
echo "CI locale réussie. À coller dans la PR :"
echo
echo "CI GitHub indisponible (budget Actions épuisé, décision D-08). Vérification locale, \`make ci\`, sur $commit$dirty :"
printf '%s\n' "${results[@]}"
if [ -n "$dirty" ]; then
    echo
    echo "Attention : des modifications non commitées ont été testées. Committez, puis relancez make ci avant de coller ce résultat." >&2
fi
