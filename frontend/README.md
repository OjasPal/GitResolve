# GitResolve Phase 1 frontend

Run `npm ci`, then `npm run dev`. The app defaults to http://localhost:3000;
the backend defaults to http://localhost:8000/api/v1. `npm run lint` and
`npm run build` validate the frontend. Webpack and Tailwind PostCSS are used
because the installed Turbopack CSS subprocess crashes on this Windows setup.
Fonts use the local system stack, so builds do not download fonts.

## Local configuration

Put Firebase web settings in `frontend/.env.local` (ignored by Git). Never put
actual project settings into tracked examples. Set these build-time variables:

- `NEXT_PUBLIC_FIREBASE_API_KEY`
- `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`
- `NEXT_PUBLIC_FIREBASE_PROJECT_ID`
- `NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET`
- `NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID`
- `NEXT_PUBLIC_FIREBASE_APP_ID`
- `NEXT_PUBLIC_API_BASE_URL` (absolute backend URL ending in `/api/v1`)
- `NEXT_PUBLIC_GITHUB_APP_URL` (your actual GitHub App installation URL)
- `NEXT_PUBLIC_HEALTH_URL` (optional; defaults to the backend `/health`)

Enable Google and Email/Password providers in Firebase Authentication and add
the frontend hostname to Authorized domains. Firebase settings alone cannot
enable providers or authorize a domain.

## Authentication and GitHub connection

Firebase handles Google/email sign-in, sign-up, password reset and persistence.
After sign-in, protected screens require a GitHub session. Identity is checked
in the background; repository discovery no longer blocks page navigation.
Repository-dependent screens show loading, empty and error states independently.
No development/demo login is exposed.

The backend accepts its own GitHub session JWT, not Firebase ID tokens. The UI
therefore uses the existing `/auth/github/login` redirect and `/auth/callback`
fragment callback to establish a separate GitHub session, then checks `/me`.
Repository screens load `/repositories` independently. Firebase tokens are never sent to the backend. The browser
session is associated with the current Firebase UID locally; this is not a
server-side Firebase/GitHub account-linking feature. Backend ownership and access
checks remain authoritative under the GitHub identity. Server-side Firebase
identity linking would require a separately designed backend contract.

Configure the existing backend in real GitHub mode, including GitHub App ID,
client ID/secret, private key, callback URL, frontend base URL, allowed CORS
origin and a secure session JWT secret. The GitHub App callback is
`http://localhost:8000/api/v1/auth/github/callback`; the frontend callback is
`http://localhost:3000/auth/callback`. Install the App on at least one repository,
then authorize GitHub. Reauthorize after installation/permission changes because
the backend refreshes installation membership during OAuth. Expired sessions
return users to connection onboarding without logging out of Firebase.

## Teammate performance

Use `npm run build` followed by `npm run start` for a shared preview. Keep the
existing tunnel pointed at the chosen frontend/backend ports. Build-time public
URLs must match that preview; changing `.env.local` requires another build.
Do not run `next dev` and `next start` as interchangeable performance baselines:
development compiles routes on first access. Measured warm production HTML
responses were faster than development; browser/hydration latency remains
unverified (see `docs/speed-validation.md` at the repository root).

The existing TanStack Query cache now deduplicates repository requests, serves
cached data immediately and revalidates after 15 seconds. It is memory-only and
clears on GitHub session changes; tokens are never query keys. Lists expose
explicit Load more controls. Next Link prefetch and lazy Shiki loading already
existed and remain in use. No new runtime dependency was added.

Checks: `node --test tests/speed.test.mjs`, `npm run lint`, `npm run build`.

## Supported flows

Landing, Google/email authentication, mandatory GitHub connection, dashboard,
repository and open-issue selection, repair creation, paginated task history,
repair details, serial status/event polling, plan and diff review, test artifacts,
and backend-authorized start/cancel/approve/reject/retry actions are connected to
the existing API. New Repair defaults to a direct bug description and submits `repoId` and `prompt`.
Users can optionally select an existing issue, submitting `repoId` and `issueNumber`.
Direct reports stay task-local and do not create GitHub issues. Repository and
issue links preselect those values in New Repair. Approval requires viewing the
complete current diff with the matching hash. No account deletion API exists,
so no misleading account deletion control is shown.

Dark is the default regardless of the operating-system preference. The toggle
switches all Tailwind dark variants and persists the chosen theme across reloads.

Live Google/email/OAuth delivery and real repair execution require the above
external configuration plus a running backend/worker and its existing sandbox,
model and GitHub setup. Fixture/fake backend modes are test modes and should
not be used for a production demonstration. Dashboard metrics describe the
latest 50 returned tasks; task history supports loading older pages.
