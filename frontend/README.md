# Frontend

React, Vite, TypeScript, Tailwind, and Headless UI dashboard for the MediaRefinery service.

The production build writes into `../src/mediarefinery/web/` so FastAPI can serve the dashboard from the same origin as the API.

The dashboard now exposes the asset-intelligence surface: review queue filters, media-kind/event filters, metadata/OCR search, event group management, adult subtype profile setup for admins, analysis badges and OCR/subtype detail panels on asset cards, and a simple custom-category builder backed by the service category JSON.

## Commands

```powershell
npm install
npm run dev
npm run typecheck
npm test
npm run build
```

During development, run the backend on `http://localhost:8080` and the Vite dev server on `http://localhost:5173`. Vite proxies `/api` to the backend.

## Privacy Contract

- No third-party CDNs, fonts, analytics, or trackers.
- Passwords and PINs stay in component state only long enough to submit the relevant request.
- The frontend does not write credentials, wizard state, dashboard state, or PINs to `localStorage`, `sessionStorage`, or IndexedDB.
- State-changing API calls use `credentials: "include"` and echo the `mr_csrf` cookie as `X-CSRF-Token`.

## Documentation

Dashboard behavior is documented in `../docs/guides/dashboard.md`. Contributor commands and validation steps are documented in `../docs/development/local-development.md`.
