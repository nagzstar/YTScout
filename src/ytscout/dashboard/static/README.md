# Vendored static assets

`ytscout dashboard` inlines these into `dashboard/index.html` at build time, so the page
renders from `file://` with no network. Nothing here is fetched at runtime; no CDN.

| File | What | Version | Licence | Source |
|------|------|---------|---------|--------|
| `chart.umd.js` | Chart.js UMD build (`dist/chart.umd.js`) | 4.5.1 | MIT (`chart.js.LICENSE.md`) | npm tarball `https://registry.npmjs.org/chart.js/-/chart.js-4.5.1.tgz`, downloaded 2026-09-24 |

SHA-256 of `chart.umd.js`: `ecc3cd1eeb8c34d2178e3f59fd63ec5a3d84358c11730af0b9958dc886d7652a`.

The build strips the trailing `//# sourceMappingURL=` comment so devtools never look for
a `.map` file beside the page. To upgrade, replace `chart.umd.js` with the new UMD build,
update this table and the hash, and rerun `pytest tests/test_dashboard.py`.
