---
name: Bug report
about: The service returned the wrong result, crashed, or behaved unexpectedly.
title: "[bug] "
labels: ["bug", "needs-triage"]
assignees: []
---

## What happened

<!-- One or two sentences on the observed behaviour. -->

## What you expected

<!-- What the docs / your intuition said should happen. -->

## Minimal reproducer

```bash
# A single curl (or docker compose run) command that reproduces the bug.
# Prefer demo mode so we don't need your Copernicus credentials.
DEMO_MODE=true docker compose up -d
curl -s "http://localhost:8000/api/events?bbox=6,38,14,43" | jq '.features | length'
```

## Environment

Run this snippet and paste the output:

```bash
curl -s http://localhost:8000/api/health
docker --version
cat /etc/os-release 2>/dev/null | head -3
```

- MHEAT version (from `/api/health`):
- Deployment: docker compose / Helm / bare uvicorn / ...
- `DEMO_MODE`:

## Logs / traceback

<!-- If the service crashed, paste the full traceback. If it returned a wrong
value, paste the response body. Please scrub credentials. -->

```
```

## Scope

- [ ] Blocking a grant milestone
- [ ] Data-quality issue (wrong events, wrong impact numbers)
- [ ] Regression from a previous MHEAT version — last known-good version: _
- [ ] Affects only demo mode / only live mode
