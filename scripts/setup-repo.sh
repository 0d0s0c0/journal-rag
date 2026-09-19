#!/usr/bin/env bash
#
# Run once after cloning. Installs the local git protections that CANNOT be
# committed, because they live in .git/config rather than in the tree:
#
#   - nbstripout clean filter  (strips notebook outputs before they are staged)
#   - core.hooksPath           (activates scripts/hooks/pre-commit)
#
# Without this, a fresh clone has NO protection against committing journal
# content, and git says nothing about it.
#
set -euo pipefail
cd "$(dirname "$0")/.."

echo "Installing nbstripout filter..."
uv run nbstripout --install --attributes .gitattributes

echo "Pointing git at scripts/hooks..."
git config core.hooksPath scripts/hooks
chmod +x scripts/hooks/*

echo
echo "Verifying:"
printf "  nbstripout filter : %s\n" \
  "$(git config --get filter.nbstripout.clean >/dev/null && echo installed || echo MISSING)"
printf "  hooksPath         : %s\n" "$(git config --get core.hooksPath || echo MISSING)"
printf "  pre-commit        : %s\n" \
  "$([ -x scripts/hooks/pre-commit ] && echo executable || echo NOT EXECUTABLE)"
echo
echo "Done. Journals belong in ../journal-data/ — never in this repo."
