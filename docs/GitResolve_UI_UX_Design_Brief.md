# GitResolve: UI/UX Design Brief

**Doc 4 of 6** | v1.0 | Save as `docs/UI_UX_DESIGN_BRIEF.md` | Sources: PRD, TRD, AFD, current `frontend/` (Next 16, Tailwind 4)

> This brief is the visual authority. If a design skill, template or library pushes blue/purple palettes, neon glows or loud gradients, **this document wins.**

## 1. Direction

**Obsidian glass, cut like a diamond.** Black, sleek, premium, in the spirit of Vercel's restraint and 21st.dev's polish, with one drop of signal red.

| Principle | Means |
|-----------|-------|
| Calm first | Neutral by default. Color appears only when it means something |
| Big and breathable | Large type, 48px controls, generous space. One job per screen |
| Diamond, not bubble | Chamfered (cut) corners, hairline edges, light sweeps. No soft pill shapes except badges |
| Depth, not decoration | Glass layers, soft shadows and tilt give 3D. No gradients for their own sake |
| Product, not template | Every line of copy sounds like a shipped product (section 9) |
| Motion with purpose | Smooth and short. If it doesn't explain something, cut it |

## 2. References (what to take, what to leave)

| Reference                             | Take | Leave |
|---------------------------------------|------|-------|
| 21st.dev black landing template       | Centered hero, pill announcement badge, two-line display headline fading white to grey, one white CTA, soft light bloom rising behind a cropped product mockup, minimal top bar | Amber bloom, "template" copy, floating dev toolbar, generic CRM mock |
| Red-accent editorial shot             | **One** red word in the headline; red light as atmosphere (rays, reflections) on dark stone; everything else monochrome | Photography, arch shape, serif type |
| Professional apps (landing structure) | Sticky nav, pill badge, huge headline, 3 CTAs, product shown below, long scrolling site with distinct sections | Light theme palette, phone mockup |

## 3. Signal Red Budget (strict)

Signal red covers **at most ~2% of any screen.** It is an accent, never a surface.

| Allowed | Forbidden |
|---------|-----------|
| One word in the hero headline | Button fills, card fills, section backgrounds |
| Faint bloom behind the hero product window (max 16% opacity, fades out by 60% height) | Borders on cards, inputs or buttons in their default state |
| Live "agent working" dot (pulses) | Charts, icons, large gradients, text blocks |
| Logo ember (one small facet) | Focus rings (these are white) |
| 1px underline under the active nav item | Anything on the GitHub authorization screens except real errors |
| Text selection tint, 404 light sweep | Decorative use anywhere else |

**Errors are separate.** Use `--danger` (lighter coral) with an icon and a message. Show red borders only **after** a user action fails or a required field is missed, never on load. Failed-task badges use the same danger style.

## 4. Design Tokens

### 4.1 Color

| Token | Dark "Obsidian" (default) | Light "Porcelain" |
|-------|---------------------------|-------------------|
| `--bg` | `#050505` | `#F4F3F1` |
| `--bg-raised` | `#0B0B0C` | `#FFFFFF` |
| `--glass` | `rgba(255,255,255,.045)` | `rgba(255,255,255,.62)` |
| `--glass-strong` | `rgba(255,255,255,.075)` | `rgba(255,255,255,.82)` |
| `--stroke` | `rgba(255,255,255,.10)` | `rgba(0,0,0,.08)` |
| `--stroke-hi` | `rgba(255,255,255,.30)` | `rgba(0,0,0,.22)` |
| `--text` | `#F5F5F4` | `#0A0A0A` |
| `--text-2` | `#A3A3A3` | `#52525B` |
| `--text-3` | `#6B6B6B` | `#8A8A8F` |
| `--signal` | `#E5342F` | `#D42A25` |
| `--danger` | `#FF6B5E` | `#C93A2E` |
| `--success` / `--warn` | `#5FB38A` / `#D9A441` (muted) | `#2F8F62` / `#B7791F` |

Status dots only (never fills): waiting = warn, success = success, active = signal pulse, failed = danger, stopped = text-3. No blue, no purple, no neon anywhere.

### 4.2 Type (load Geist: the current CSS falls back to Arial)

| Role | Size / line | Notes |
|------|-------------|-------|
| Display | 80/84 (mobile 44/48), -0.04em, weight 500 | Hero only. Gradient `--text` to `#9A9A9A` |
| H1 / H2 / H3 | 48 / 36 / 24 | Weight 500, tight tracking |
| Body | 17/28 | Weight 400, `--text-2` |
| Small / label | 14 / 12 uppercase +0.08em | |
| Mono (Geist Mono) | 13/20 | Repo names, hashes, diffs, stage names |

### 4.3 Size and spacing (big by default)

| Item | Value |
|------|-------|
| Base unit | 8px grid |
| Buttons | 48px high default, 56px hero, min 44px touch target |
| Inputs / selects | 48px high, 16px text (prevents iOS zoom) |
| Cards | 28-32px padding, 24px gap |
| Section padding (landing) | 112px vertical (64px mobile) |
| Content width | 1200px app, 1280px landing, hero window up to 1120px |
| Nav height | 72px public, 64px app |

### 4.4 Chamfer, edges and glass

Chamfer sizes: **10px** buttons/inputs, **16px** cards, **24px** hero window and modals. Rounded corners are only for badges (full pill) and avatars.

```css
.facet { /* cut corners */
  --c: 16px;
  clip-path: polygon(var(--c) 0, calc(100% - var(--c)) 0, 100% var(--c),
    100% calc(100% - var(--c)), calc(100% - var(--c)) 100%, var(--c) 100%,
    0 calc(100% - var(--c)), 0 var(--c));
}
.glass { background: linear-gradient(180deg, rgba(255,255,255,.07), rgba(255,255,255,.03));
  backdrop-filter: blur(24px) saturate(140%); }
```

- **Hairline edge:** a `::before` with the same `clip-path`, 1px inset, gradient `--stroke-hi` (top-left) fading to `--stroke`. Gives the faceted light-catch.
- **Shadows:** `clip-path` cuts `box-shadow`. Put `filter: drop-shadow(0 24px 40px rgba(0,0,0,.6))` on a **wrapper** element.
- **Extras:** inner top highlight (`inset 0 1px 0 rgba(255,255,255,.10)`), 3% film grain over the page background.
- **Blur budget:** max 3 blurred layers visible at once. Fallback without `backdrop-filter`: solid `--bg-raised`.

### 4.5 Motion

| Token | Value |
|-------|-------|
| Ease | `cubic-bezier(0.22, 1, 0.36, 1)` |
| Fast / base / slow | 150ms / 250ms / 600ms |
| Stagger | 60ms between siblings |
| Hover (buttons, cards) | lift 1px + edge light sweep (600ms), no glow |
| Press | scale .98, 100ms |
| Page enter | fade + 8px rise, 250ms |
| Tilt (hero window) | rotateX 6deg, pointer-follow +/-4deg, spring (stiffness 120, damping 20) |
| Reduced motion | opacity fades only, no tilt, no parallax, no sweep |

## 5. Components

| Component | Spec |
|-----------|------|
| **Primary button** | White fill (light theme: black), black text, 10px chamfer, subtle top sheen. Hover: sheen sweep. Never red |
| **Secondary button** | Glass fill, `--stroke` edge, white text |
| **Ghost / link** | Text only, underline slides in on hover |
| **Icon button** | 44px glass facet, 20px icon (Lucide, 1.5 stroke) |
| **Input** | Glass, 48px, label above (not placeholder-only), focus = white hairline + 2px outer ring. Error appears **after blur or submit** |
| **Card** | `.glass.facet` 16px, hairline edge. Interactive cards lift 2px and sweep light on hover |
| **Badge / status chip** | Full pill, mono 12px, 6px status dot + label (section 4.1) |
| **Nav item** | 44px, icon + label, active = `--glass-strong` + 1px signal underline (top bar) or 2px signal left tick (sidebar) |
| **Stepper** | Numbered facets joined by a hairline. Done = white check, current = white ring, upcoming = dim |
| **Timeline** | Vertical hairline with stage nodes, events fade in as they arrive, mono timestamps |
| **Diff viewer** | Mono 13px, Shiki-highlighted in monochrome plus muted green/red gutters, line numbers, hunk headers sticky |
| **Modal** | 24px chamfer, blurred scrim, focus trap, Esc closes |
| **Toast** | Bottom-right glass facet (Sonner), 4s, never red unless an error |
| **Skeleton** | Glass blocks with a slow light sweep. Match final layout |
| **Command palette** | Cmd/Ctrl+K (cmdk): jump to pages, repos, tasks, New Repair |

Interaction states required for every interactive element: **default, hover, focus-visible, active, disabled, loading, error, empty.** Disabled = 40% opacity and `cursor: not-allowed`. Nothing on the public site may look disabled.

## 6. App Shell

| Area | Public site | Signed-in app |
|------|-------------|---------------|
| Navigation | Sticky top bar 72px: logo, **Product, How it works, Safety, FAQ**, theme toggle, **Sign in** (ghost), **Get started** (primary). All live. Glass appears after 8px scroll. Mobile: sheet menu | Collapsible left sidebar (264px, 72px collapsed): Dashboard, Repair Tasks, Repositories, Settings, **New Repair** pinned at top. Under 768px it becomes a drawer |
| Top utility bar | n/a | 64px: breadcrumb, Cmd+K search, theme toggle, backend status dot, avatar menu (account, settings, sign out) |
| Scroll | Lenis smooth scroll | Native scroll (Lenis off) |
| Footer | Full footer | Single quiet line |

The email currently shown on the dashboard account card moves into the avatar menu.

## 7. Page Specs

### 7.1 Landing `/` (long, scrolling, Vyana-style structure, 21st.dev look)

| # | Section | Content and visual |
|---|---------|--------------------|
| 1 | **Hero** | Pill badge: "Every pull request needs your approval" with arrow. Display headline, two lines: **"Ship the fix. / Keep the *veto*."** (*veto* in signal red, the only red word). Subhead (see 9). CTAs: **Continue with GitHub** (primary, 56px) + **See a live run** (secondary). Faint red bloom rising behind the product window |
| 2 | **Product window** | Real GitResolve task screen (timeline + diff) in a chamfered glass window, 3D-tilted, cropped at the fold with a bottom fade into the next section. On scroll it levels out to 0deg |
| 3 | **Works with** | Quiet text strip: GitHub, Python, pytest, Docker. No fake customer logos |
| 4 | **How it works** | 6 steps pinned while scrolling: Receive, Investigate, Reproduce, Repair, Verify, Submit. Each step swaps the window content |
| 5 | **Live run** | Auto-replaying run: issue in, failing test, patch, tests pass, PR ready. Pauses off-screen |
| 6 | **Careful by design** | 6-card bento: isolated sandbox, bounded attempts, diff safety gate, scoped GitHub access, full audit trail, you approve every PR |
| 7 | **Guarantees** | Three large numerals (CountUp), policy facts only: **3** attempts max, **0** auto-merges, **100%** human-approved PRs |
| 8 | **FAQ** | 6 accordion items, glass facets |
| 9 | **Final CTA** | Centered line + primary button, light sweep on enter |
| 10 | **Footer** | Logo, links (Product, Safety, FAQ, GitHub), one quiet line |

### 7.2 Sign in / Sign up / Reset `/login` `/register` `/reset-password`

- Split layout: left glass panel with a looping run replay and one sentence; right form (max 440px).
- Order: **Continue with GitHub** (primary), Continue with Google, divider, email + password.
- Inline validation after blur. Password strength meter (neutral to white, never red). Show/hide password.
- Success: field check mark, button morphs to a spinner, then routes on.
- Reset password: single field, confirmation state with the email shown.

### 7.3 GitHub Authorization `/onboarding` (and the in-app gate)

- A calm 4-step stepper: **Account, Install app, Choose repositories, Ready.**
- Main card: what GitResolve can do, in two columns: **Reads now** (code, issues, repo metadata) and **Writes only after you approve** (a branch and a pull request).
- Primary button **Install GitHub App**; secondary **I've installed it, refresh access**.
- Repository picker after install: search, private/public tag, language dot, select to continue.
- **Default state is all neutral.** States: Not connected (dim), Connecting (spinner), Connected (white check), Needs attention (warn dot + plain explanation + one action).
- Red appears only for real failures (access revoked, GitHub unreachable), as an inline message with a Retry. Never red lines on first view.

### 7.4 Dashboard `/dashboard`

| Block | Spec |
|-------|------|
| Header | "Good evening, {name}." + one line of status ("1 fix is waiting for your review.") + **New Repair** |
| Needs your attention | Large card for tasks in `awaiting_start` / `awaiting_approval`: repo, issue, summary, primary action (**Review fix**). Hidden when empty |
| Stat tiles (4) | Active, Awaiting you, PRs opened, Stopped. Clickable (filters Repair Tasks), 7-day sparkline in white/grey |
| Active run | Live card: stage ring, elapsed time, attempt dots, signal pulse dot |
| Recent tasks | Roomy rows: repo avatar, title, source chip (Issue #42 / Direct report), status chip, attempt dots (●●○), PR chip, date. Hover lifts row |
| Empty state | Faceted crystal graphic + "No repairs yet." + **Start your first repair** |

### 7.5 New Repair `/tasks/new`

Large segmented control **From an issue / Describe a bug**. Issue mode: repo selector, searchable issue list, live preview. Describe mode: textarea with example prompt. Right rail shows limit chips (3 attempts, 20 min, you approve). One primary button: **Analyze**. The next step is the plan on the task page.

### 7.6 Task Detail `/tasks/[taskId]` (the flagship screen)

- Header: repo, source chip, status chip, attempts, elapsed, **Cancel** (when allowed).
- Left (40%): animated vertical **timeline**, auto-follow toggle. Right (60%): tabs **Plan, Evidence, Diff, Tests**.
- Sticky bottom action bar by status: **Start repair**, **Approve & create PR** + **Reject**, **Open pull request**, **Run again**.
- Approve opens a modal summarising branch, files, lines and tests. One light sweep when the PR is created, then calm.
- Failure states show cause, evidence and the recovery action (per AFD 7.2).

### 7.7 Repair Tasks `/tasks`, Repositories `/repositories`, Settings `/settings`

- **Tasks:** table with status-group filters, repo filter, search; row hover reveals actions. Load-more paging.
- **Repositories:** cards with name, visibility, language, default branch, **Fix an issue**; **Add or remove repositories** and **Refresh access** at the top.
- **Settings:** left sub-nav: Account, GitHub access, Appearance (dark / light / system with preview cards), Danger zone. Red appears only inside Danger zone, with a confirm step.

### 7.8 404 and system pages

| Page | Design |
|------|--------|
| **404** | Giant faceted glass "404" with a slow signal-red light sweep across the numerals. Line: **"Page not found. Unlike your bugs, this one isn't ours to fix."** Buttons: **Go to dashboard** (or Home when signed out), **Back** |
| 500 / error | Same layout, "Something broke on our side. It's logged and we're on it." + Try again |
| Session expired | Calm glass card, "Your session ended." + **Sign in again** (returns to the saved page) |
| Offline / no worker | Inline banner on the task page, never a full-page error |

## 8. Motion and Scroll Rules

- **Lenis** (`lenis/react`): public routes only. Disable in the app shell. Add `data-lenis-prevent` on diff, log and modal scrollers. Honor reduced motion.
- **Motion** (`motion/react`): section reveals, stagger lists, layout transitions, presence for modals and toasts, hero tilt.
- Animate `transform` and `opacity` only. Reveal once, never loop except the live replay and the live dot.
- Cap parallax at 24px. No scroll-jacking beyond the pinned "How it works".

## 9. Copy Rules

Write like a shipped product: short, confident, specific. No developer notes, no placeholder text, no "Lorem", no exclamation marks. **One** dry line per page at most.

| Place | Copy |
|-------|------|
| Hero subhead | "GitResolve reads the issue, reproduces the bug, tests a fix in an isolated sandbox, and opens a pull request. Nothing merges without you." |
| Primary CTAs | Continue with GitHub, See a live run, Review fix, Approve & create PR, Run again |
| Statuses | Queued, Analyzing issue, Plan ready, Working, Fix ready, Creating pull request, Pull request opened, Couldn't reproduce, No fix found, Stopped by safety check, Cancelled |
| Empty tasks | "No repairs yet. Pick an issue and let GitResolve investigate." |
| GitHub intro | "Choose what GitResolve can see. You approve every change before it leaves." |
| Errors | Say what happened and the next step: "GitHub didn't respond. Try again in a moment." |
| Footer line | "GitResolve. Autonomous engineering, human final say." |

## 10. Stack and Where It Is Used

| Library | Use | Rule |
|---------|-----|------|
| `geist` (next/font) | Type | Replaces the Arial fallback |
| shadcn/ui + Radix | Accessible primitives (Dialog, Tabs, Dropdown, Tooltip) | Restyle with our tokens |
| `next-themes` | Dark/light/system, no flash | Replaces custom ThemeContext |
| `motion`, `lenis` | Section 8 | |
| `class-variance-authority`, `clsx`, `tailwind-merge` | Variants | |
| `cmdk`, `sonner` | Command palette, toasts | |
| `@tanstack/react-query` | Timeline polling every 3s, stops on terminal states | |
| `shiki` | Diff and code highlighting | Monochrome theme |
| **React Bits** (copy-paste via shadcn CLI) | **SpotlightCard** (feature cards), **ShinyText** (badge), **BlurText/SplitText** (hero headline), **CountUp** (guarantees), **TiltedCard/GlareHover** (hero window), **GlassSurface** (nav, 1-2 surfaces) | Landing only, desktop first. One WebGL background max (**DarkVeil**, **Beams** or **Silk**) in the hero with a static fallback |
| 21st.dev, Magic UI, Aceternity | Reference and component sources (nav, hero, FAQ, auth, sidebar, bento, Border Beam) | Check each license, re-skin to tokens |
| UI/UX Pro Max (AI skill) | Design-system and checklist helper for the editor | Pin this brief as source of truth |
| **Do not use** | Aurora, Particles, GlitchText, SplashCursor, Ballpit, confetti, any WebGL inside the app | |

## 11. Responsive, Accessibility and Performance

- Breakpoints: 640 / 768 (sidebar to drawer) / 1024 / 1280. Test 375, 768, 1440, 1920.
- Contrast AA on glass (check text over blur). Focus-visible ring is white, 2px, offset 2px. Full keyboard path. Real labels, `aria-live` for task status changes.
- Respect `prefers-reduced-motion` and `prefers-reduced-transparency`.
- Budgets: LCP under 2.5s, hero JS lazy-loaded, no layout shift from fonts or images, WebGL only on landing and only above 1024px.
- Diffs and artifacts render as **plain text**, never HTML.

## 12. Quick Wins (do first)

1. Hide the dev badge: `devIndicators: false` in `next.config.ts` (dev-only overlay).
2. Load Geist and Geist Mono; delete the Arial fallback variables.
3. Make every top-bar link live on the public site; add Sign in and Get started.
4. Replace the "GR" tile with an original faceted mark. Do not ship an altered Octocat; GitHub's logo rules discourage it.
5. Add `app/not-found.tsx` and `app/error.tsx` in the new theme.

## 13. Build Order and Acceptance

**Order:** tokens and shell, then components, then landing, then auth and GitHub authorization, then dashboard and tasks, then settings and system pages, then polish.

| # | Acceptance check |
|---|------------------|
| 1 | Dark and light themes both pass AA contrast; toggle persists and never flashes |
| 2 | Signal red appears only where section 3 allows (visual review against the table) |
| 3 | No blue, purple or neon anywhere; no red borders on first load of any form |
| 4 | Every public-site control is clickable and has hover, focus and active states |
| 5 | All controls meet 44px minimum; default buttons are 48px |
| 6 | Landing shows all 10 sections and scrolls smoothly with Lenis; the app shell scrolls natively |
| 7 | Reduced-motion users get no tilt, parallax or sweeps |
| 8 | 404, 500 and session-expired pages exist and match the theme |
| 9 | GitHub authorization screen has a neutral default; red shows only on a real failure |
| 10 | Landing LCP under 2.5s on a mid-range laptop; blur layers never exceed 3 |

**Master prompt for the AI editor:** "Follow `docs/UI_UX_DESIGN_BRIEF.md` exactly. Black glass UI with chamfered edges, Geist type, big controls, signal red only as allowed in section 3. Use shadcn, motion and Lenis as specified. Do not add blue, purple, neon or extra animations."

*Next: Document 6, Implementation Plan.*
