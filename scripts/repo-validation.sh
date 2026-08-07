#!/usr/bin/env bash
# Repo-specific validation, run by .ai-policy/scripts/project-validation.sh after
# the policy layer's own checks. Its result is what the commit and push gates in
# .ai-policy/policy.env read, so what runs here is what a `passed` marker means.
#
# Scope is the backend suite, deliberately. See the two notes below before adding
# to it: this script runs on the path to every commit, and a gate that makes the
# normal path painful gets routed around, which costs more than it protects.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> backend tests"
# ~2700 tests in under 30s, so this is affordable on every commit. The suite opts
# out of backend/.env (#752), so it resolves the same code defaults CI resolves.
make backend-test

# Frontend lint+build is deliberately NOT run here, for two reasons:
#
#   1. `npm run test` is `next lint && next build`, minutes rather than seconds.
#   2. `next build` writes the same .next/ directory a running `next dev` server
#      owns, and corrupts its chunks. A check that fires on every commit would
#      break the dev server as a matter of routine.
#
# CI runs the frontend-test job on every push and pull request
# (.github/workflows/deploy.yml), so the coverage is not lost, only moved to
# where its cost is free. Run `make frontend-test` by hand before a
# frontend-touching push, with `next dev` stopped.
echo
echo "==> NOT run here: frontend lint+build (CI job 'frontend-test' covers it),"
echo "    and the integration-marked backend tests (excluded from the baseline)."
