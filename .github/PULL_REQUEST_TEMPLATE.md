<!-- Thanks for contributing to MHEAT! -->

## Summary

<!-- One or two sentences. What changed, and why. -->

## Pass number

<!--
MHEAT tracks work as numbered "passes" in CHANGELOG.md. If this PR is
part of a planned pass, put the number here. Otherwise write "n/a".
-->

Pass: _

## Changes

- [ ] Backend (`backend/app/**`)
- [ ] Frontend (`frontend/src/**`)
- [ ] Helm chart / deploy (`charts/`, `docker-compose.yml`, `Dockerfile`)
- [ ] Scripts (`scripts/**`)
- [ ] Docs (`README.md`, `CHANGELOG.md`, `docs/**`, `tutorials/**`)
- [ ] CI (`.github/workflows/**`)

## Verification

<!-- What did you actually run locally? Copy/paste the outputs. -->

- [ ] `cd backend && DEMO_MODE=true pytest` — all tests green, coverage gate passes.
- [ ] `cd frontend && npx vitest run` — all tests green.
- [ ] `cd frontend && npm run build` — vite build succeeds.
- [ ] `cd frontend && npm run lint` — ESLint exits 0.
- [ ] (if touching scripts) `DEMO_MODE=true python scripts/reproduce.py` — manifest hashes match `docs/reproducibility.md`, OR the doc has been updated with the new hashes.

## CHANGELOG

- [ ] I added a `- **Feature** — ... (Pass N)` bullet under `[Unreleased]` in `CHANGELOG.md`.

## Security

- [ ] No new secrets committed.
- [ ] New Python deps audited (`pip-audit` passes in CI).
- [ ] New Node deps audited (`npm audit --omit=dev` passes in CI).

## Notes for the reviewer

<!-- Anything the diff alone can't tell them. Known regressions, follow-ups, open questions. -->
