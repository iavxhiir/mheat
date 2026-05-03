import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';
// In dev mode we proxy /api to the FastAPI server on :8000 so developers
// can run `npm run dev` against a backend started with `uvicorn`.
export default defineConfig({
    plugins: [
        react(),
        VitePWA({
            registerType: 'autoUpdate',
            includeAssets: ['favicon.svg'],
            manifest: {
                name: 'MHEAT - Mediterranean Marine Heatwaves',
                short_name: 'MHEAT',
                description: 'Detect Mediterranean marine heatwaves on Copernicus Marine SST. EDITO-hosted.',
                theme_color: '#0f1620',
                background_color: '#0f1620',
                display: 'standalone',
                start_url: '/',
                icons: [
                    {
                        src: '/pwa-192.png',
                        sizes: '192x192',
                        type: 'image/png',
                    },
                    {
                        src: '/pwa-512.png',
                        sizes: '512x512',
                        type: 'image/png',
                    },
                ],
            },
            workbox: {
                // Cache API responses: last /api/events, overlays, anomaly PNGs.
                maximumFileSizeToCacheInBytes: 6 * 1024 * 1024,
                runtimeCaching: [
                    {
                        urlPattern: /\/api\/events(\?|$)/,
                        handler: 'NetworkFirst',
                        options: {
                            cacheName: 'mheat-events',
                            expiration: { maxEntries: 10, maxAgeSeconds: 86400 },
                            networkTimeoutSeconds: 3,
                        },
                    },
                    {
                        urlPattern: /\/api\/overlays\//,
                        handler: 'StaleWhileRevalidate',
                        options: {
                            cacheName: 'mheat-overlays',
                            expiration: { maxEntries: 10, maxAgeSeconds: 7 * 86400 },
                        },
                    },
                    {
                        // Anomaly PNGs change on every backend tweak (climatology
                        // refresh, land-mask update, colormap change). CacheFirst
                        // would serve a 7-day-stale image after any of those —
                        // we hit that exact bug on 2026-05-03 where a pre-fix
                        // bleeding-over-land PNG persisted in the browser through
                        // multiple server-side fixes. NetworkFirst with a short
                        // TTL keeps offline-mode benefit (cache survives network
                        // outage) without pinning a stale render.
                        urlPattern: /\/api\/anomaly\?/,
                        handler: 'NetworkFirst',
                        options: {
                            cacheName: 'mheat-anomaly',
                            expiration: { maxEntries: 30, maxAgeSeconds: 3600 },
                            cacheableResponse: { statuses: [0, 200] },
                            networkTimeoutSeconds: 5,
                        },
                    },
                    {
                        urlPattern: /\/api\/health$/,
                        handler: 'NetworkFirst',
                        options: {
                            cacheName: 'mheat-health',
                            networkTimeoutSeconds: 2,
                        },
                    },
                ],
            },
        }),
    ],
    server: {
        port: 5173,
        proxy: {
            // VITE_API_TARGET lets ops point the dev server at a backend on a
            // non-default port (e.g. when running `uvicorn --port 8001` to avoid a
            // collision). Falls back to the conventional :8000 the .env defaults to.
            '/api': {
                target: process.env.VITE_API_TARGET ?? 'http://localhost:8000',
                changeOrigin: true,
            },
        },
    },
    build: {
        outDir: 'dist',
        sourcemap: false,
        chunkSizeWarningLimit: 2000,
        rollupOptions: {
            output: {
                // Split Plotly into its own chunk so it only loads when the chart
                // mounts. Main bundle drops from ~4.8 MB to ~300 KB this way.
                manualChunks(id) {
                    if (id.includes('node_modules/plotly.js'))
                        return 'plotly';
                    if (id.includes('node_modules/react-leaflet') || id.includes('node_modules/leaflet'))
                        return 'leaflet';
                    if (id.includes('node_modules/react-dom') || id.includes('node_modules/react/') || id.endsWith('node_modules/react') || id.includes('node_modules/scheduler'))
                        return 'react';
                },
            },
        },
    },
});
