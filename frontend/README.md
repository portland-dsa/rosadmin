# Portland DSA File Sharing

## Running locally

```
npm install
npm run dev
```

The app talks to an in-memory mock by default, so it runs without a backend.
Members and groups are seeded in `src/api/mock.ts`.

To point at the real backend, copy `.env.example` to `.env`, set `VITE_USE_MOCK=false`, and set `VITE_API_BASE` to the backend's URL (e.g. http://localhost:8000). Leave it empty only if the backend is served from the same origin as the app.

## Layout

- `src/types.ts` frontend types; converted from backend types in the api client, and leveraged in components. 
- `src/api/` — the backend contract. `mock.ts` and `client.ts` are the two implementations, and `index.ts` selects one. Swapping the mock for the real backend is a single environment flag.
- `src/components/` all frontend content and state
