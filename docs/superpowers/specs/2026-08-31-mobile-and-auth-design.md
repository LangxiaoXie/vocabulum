# Vocabulum — Mobile Interface & Authentication

**Date:** 2026-08-31
**Status:** Approved design, pending implementation plan

## Context

Vocabulum is a single-file browser SRS vocabulary app (`index.html`, ~1130 lines: markup,
CSS, and JS in one document). It has:

- A fixed 260px sidebar plus a flex main column, and **zero `@media` rules** — the layout
  is desktop-only and unusable on a phone.
- **No authentication of any kind.** All state lives in one `localStorage` key, `vocab2`.
- Views toggled by inline `style.display` in `showView()`: `#view-dashboard`, `#view-fc`,
  `#view-done`, plus `#purchase-modal` and `#view-settings` overlays.

This design adds a required login gate and a responsive mobile interface.

## Decisions

| Question | Decision |
|---|---|
| Auth depth | Real Google + email now, via Supabase |
| Mobile shape | Responsive, one `index.html` |
| Gating | Login required; no guest mode |
| Swipe-to-rate | Out. Buttons only |
| Credentials | Build against placeholders; user fills in `config.js` later |

### Consequence: no more `file://`

Supabase auth and Google OAuth both require a real origin. The app must be served over
HTTP (`python3 -m http.server 8000`) or deployed. This is a deliberate, accepted trade.

### Consequence: the app is locked until configured

"Required, no guest" means an unconfigured `config.js` yields no way in. Rather than a
broken form, the login screen renders a **setup panel** naming the exact missing values.

## Architecture

Two new files. Everything else stays in `index.html`, preserving the project's
single-file character.

```
vocabulum/
├── config.js     (new)  credentials + provider enable flags
├── auth.js       (new)  provider adapters, session lifecycle, login controller
└── index.html    (mod)  #view-auth markup, mobile shell, @media block
```

### Component 1 — `config.js`

```js
window.VOCAB_CONFIG = {
  supabaseUrl: '',        // https://<ref>.supabase.co
  supabaseAnonKey: '',    // publishable/anon key — safe to commit
  providers: { google: true, apple: false, wechat: false },
  wechat: { appId: '', exchangeEndpoint: '' },
};
```

The Supabase anon key is designed for public exposure (row-level security is the actual
guard), so committing it is safe. A provider is *offered* only when its flag is true
**and** the client is configured.

### Component 2 — `auth.js`

Loads `@supabase/supabase-js@2.112.4/dist/umd/supabase.js` (verified: HTTP 200, 212 KB,
exposes the global `supabase`). Exposes one module:

```
Auth.init()            → { configured: bool, user: User|null }
Auth.onChange(cb)      → fires on sign-in, sign-out, token refresh, OAuth return
Auth.signUpEmail(email, password)
Auth.signInEmail(email, password)
Auth.resetPassword(email)
Auth.signInProvider('google' | 'apple' | 'wechat')
Auth.signOut()
Auth.currentUser()
```

Mapping to supabase-js v2: `getSession`, `onAuthStateChange`, `signUp`,
`signInWithPassword`, `resetPasswordForEmail`, `signInWithOAuth({provider, options:
{redirectTo}})`, `signOut`.

A provider table drives both the UI and dispatch:

| Key | Kind | Ships as | Requires |
|---|---|---|---|
| `email` | native | **enabled** | Supabase project only |
| `google` | supabase-oauth | **enabled** | Google Cloud OAuth client pasted into Supabase |
| `apple` | supabase-oauth | disabled | Apple Developer account ($99/yr) → Service ID + key |
| `wechat` | custom | disabled | WeChat Open Platform site app + server `code`→token exchange |

**Security invariant:** an unconfigured or disabled provider renders in an explicit
unavailable state and **must never mint a session**. With no guest mode, a mock button
that fakes success is a hole straight through the login wall. This invariant is the
single most important rule in this design.

Adding WeChat later is one table entry plus one adapter function — no UI edits.
`exchangeEndpoint` receives the WeChat `code` and returns a Supabase session; that server
is out of scope here.

**Email confirmation:** Supabase requires email confirmation on sign-up by default, so
`signUpEmail` must surface a "check your inbox" state rather than assuming an
immediate session.

### Component 3 — The gate and per-user storage

`#view-auth` is present in the HTML and **visible by default**, so the app never flashes
before the session check resolves. `app.init()` awaits `Auth.init()`; no session keeps the
login screen up, a session boots the app. `Auth.onChange` handles token expiry and the
OAuth redirect return.

Storage moves from `vocab2` to **`vocab2:<user.id>`**, so two accounts in one browser do
not share a streak.

**One-time legacy adoption.** On first sign-in, if `vocab2:<uid>` is absent, the legacy
`vocab2` blob is copied into it and `vocab2:legacyAdoptedBy` is set to that uid. The
legacy key is *never deleted* (cheap insurance), and the flag stops a second account from
also claiming it. This preserves the existing streak and SRS history.

`signOut()` clears in-memory state and returns to the gate.

### Component 4 — Login UI

Mobile-first, works at every width. One card: wordmark, email + password fields, primary
submit, a sign-up/sign-in toggle, "forgot password", a divider, then provider buttons in
brand-correct styling (Google white/grey, Apple black, WeChat green) with unavailable ones
visibly inert. Inline error text under the form — no `alert()`. Loading state on submit to
prevent double-fire.

### Component 5 — Mobile interface

A single `@media (max-width: 760px)` block appended to the existing `<style>`.

- **Shell:** `body` stops being a flex row; `100dvh` so iOS browser chrome cannot clip the
  card. `#sidebar` becomes an off-canvas drawer (`position:fixed`, `translateX(-100%)`,
  `.open` slides in) behind a scrim. The scroll-snap deck list keeps working inside it.
- **New chrome:** a top app bar (hamburger + current deck name) and a fixed bottom tab bar
  with `env(safe-area-inset-bottom)` padding. Three tabs: **Home** (dashboard), **Learn**
  (starts a session), **Profile** — a new panel showing the signed-in email, sign-out, and
  links to the existing Settings and Unlock modals, which lose their sidebar entry point on
  mobile. The tab bar hides during a flashcard session so it cannot be tapped mid-review.
- **Dashboard:** title 70px → 38px; the 4-across `.stats-row` → 2×2; `.charts-grid` → one
  column; the 52-week heatmap gets an `overflow-x:auto` wrapper with a min-width inner grid
  so cells stay square instead of collapsing into slivers.
- **Flashcard:** word 58px → 38px; the four rating buttons become a full-width 2×2 grid at
  ≥48px tall in the thumb zone; Q/W/E/R hints hidden on touch.
- **Overlays:** purchase panel → bottom sheet; settings drawer 340px → 88vw.
- Pinch-zoom stays unblocked (accessibility). Adding `theme-color` and iOS web-app meta.

## Out of scope

- **Cloud sync of SRS progress.** Progress stays in `localStorage`, namespaced per user.
  The same account on a second device starts fresh. The seam exists (state is a single
  JSON blob keyed by uid); a `user_state` table with last-write-wins is a small follow-up.
- The WeChat token-exchange server.
- Real payments — the purchase modal stays mock.

## Verification

No test framework exists in this repo; verification is by driving the real app.

1. Serve over `python3 -m http.server`; drive Chrome at 390×844 and at desktop width.
2. Console clean on every view: login, dashboard, flashcard, done, settings, purchase.
3. **Gate:** with empty config, the setup panel shows and no provider button yields a
   session. Disabled providers are inert.
4. **Migration:** seed a legacy `vocab2` blob, sign in, confirm streak and SRS survive
   into `vocab2:<uid>`, and that a second uid does not adopt it.
5. **Layout:** no horizontal body scroll at 320px, 390px, and 768px.

**Honest limit:** without a Supabase project, a real Google or email round-trip cannot be
exercised. Gate logic, layout, and the unconfigured path are fully verifiable; the live
credential path needs the user's project and is confirmed by them.

## Setup steps for the user (later)

1. supabase.com → new project (free). Settings → API → copy **Project URL** and **anon
   key** into `config.js`.
2. Google: Cloud Console → OAuth client (Web) → paste client ID + secret into Supabase
   Auth → Providers → Google. Add the Supabase callback URL as an authorized redirect URI.
3. Serve over HTTP and add that origin to Supabase Auth → URL Configuration.
