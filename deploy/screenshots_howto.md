# MHEAT — Screenshot capture for the EDITO FSTP grant proposal

Five screenshots are used to illustrate the dashboard in the proposal narrative. Each is a PNG, min. 1600 px wide, captured against the running container in DEMO_MODE so there's nothing to set up beyond `docker compose up -d --build`.

## Prerequisites

```bash
cd mheat
docker compose up -d --build
# wait for /api/health to return 200
curl -sf http://localhost:8000/api/health
```

Open <http://localhost:8000/> in Chrome/Firefox at **1920×1080** (DevTools → Device Toolbar → Responsive 1920×1080, or a real second monitor).

## Target filenames

| # | File | Shot description |
|---|------|------------------|
| 1 | `01_overview_map.png` | Full dashboard — header, sidebar, map with 9 clusters visible, Plotly timeline below, in window 2022-05-15 → 2022-09-15. |
| 2 | `02_event_cluster_detail.png` | Click the large Category-V cluster in the Tyrrhenian (~10°E/41°N); event panel on the left shows dates, duration, intensity and Impact block (2 aquaculture sites, 191 km² MPA, 1399 km² seagrass). |
| 3 | `03_impact_panel.png` | Zoom to 8°E..15°E, 39°N..43°N so aquaculture pins, MPA polygons (teal) and seagrass polygons (green) are all visible together with event outlines. |
| 4 | `04_timeline_chart.png` | Crop of the Plotly bar-chart timeline (bottom of the map area), showing horizontal bars coloured by category. |
| 5 | `05_anomaly_overlay.png` | Anomaly layer toggled ON with anomaly-date slider set to 2022-07-20 — a clear red blob over the Tyrrhenian confirms the dataset's synthetic heat plume. |

## Manual capture procedure (recommended)

1. Boot the stack, hit <http://localhost:8000/> at 1920×1080, wait for the map to finish loading.
2. Open DevTools, press Ctrl/Cmd + Shift + P, run **"Capture full size screenshot"** — exports a viewport PNG.
3. Save into `deploy/screenshots/` with the filenames above.

## Headless CLI capture (optional, Chromium)

If you have Chromium or Chrome installed locally:

```bash
# Full page, viewport 1920x1080
chromium --headless --disable-gpu --hide-scrollbars \
  --window-size=1920,1080 \
  --screenshot=deploy/screenshots/01_overview_map.png \
  http://localhost:8000/
```

For the click-through shots (2-5) this path needs a real Puppeteer/Playwright script. A minimal one is stubbed in `scripts/shots.mjs` (not required for submission; the manual path is canonical).

## Verification checklist before sending to the grant portal

- All 5 PNGs exist under `deploy/screenshots/`.
- Each is ≥ 1600 px wide and < 2 MB.
- DEMO_MODE badge is visible on at least shot #1 (shows reviewers the stack is self-contained).
- The `N clusters on screen` badge in the top-right of the map is visible on shots #1, #2, #5.
- Timeline bars match the cluster count shown in the sidebar.
