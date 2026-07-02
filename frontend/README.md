# Portland DSA File Sharing

Admin panel for chapter leaders to add members to Google Groups (branches,
working groups, committees, and other chapter bodies) and grant access to the
associated Google Drive folders.

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


## Endpoints required for current functionality:

```
1. GET /auth/login/discord
  Begin Discord login
→ 302 to Discord; the OAuth callback (backend-internal) sets the
  httponly session cookie and redirects back to the app


2. GET /auth/alive
→ 200 {"alive": true | false, "member": {"id": "...", "name": "..."}}
(updated: needs name and id for new display features)

3. POST /auth/logout
ends the session; clears the httponly cookie
→ 200
(new: the footer Log out control needs this — only the server can clear an httponly cookie)

4. GET /bodies
→ 200 [{"id": "...", "name": "..."}, ...]
Note this is an array rather than an object whose keys are ids.
(updated: body type not needed)

5. GET /bodies/info
spec's /bodies/info; the group id is sent in a request header
(the spec puts the input in a JSON body on a GET, which browsers can't send)
header: X-Chapter-Body: <groupId>
→ 200 {"name": "...", "id": "...", "members": {"<memberId>": {"name": "...", "email": "...", "role": "..."}, ...}}
→ 403 if the caller's scope doesn't cover the group
(updated: each member needs an email — every roster card displays it)


6. GET /members/search

(updated: the email is sent in a request header, keeping it out of the URL)

header: X-Member-Email: <email>
→ 200 {"matches":[{"id": "...", "name": "...", "email": "..."}, ...], "expired_matches": number}
(updated: matches need an email for the pending-add card)


7. PUT /members/groups/update
per spec
body:   {"id": "<memberId>", "groups": [{"id": "<groupId>", "role": "member", "remove": boolean}]}
→ 200
Frontend currently only passes a single group in the list.
-> 207 handled in the frontend as a failure.
```
