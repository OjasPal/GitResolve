# GitResolve premium UI implementation guide

Read [the GitResolve UI/UX Design Brief](GitResolve_UI_UX_Design_Brief.md) first. It owns the visual direction. This guide explains how the current frontend achieves it. For bug fixes, use the running UI and the owning source to settle implementation details. The brief includes early proposals such as three repair attempts, a live demo and public-only Lenis. The current UI shows six attempts, labels its product walkthrough as illustrative, and mounts Lenis through the shared provider. Keep proposed changes separate from the fix.

## Build the material before adding effects

- Start with near-black `--bg` (#050505), raised obsidian `--bg-raised` (#0b0b0c), off-white `--text` (#f5f5f4) and grey `--text-2` (#a3a3a3). The glass layers are 4.5% and 7.5% white. The 12% white `--stroke` separates surfaces, while the 30% `--stroke-hi` catches light on selected edges. Reuse the paired porcelain tokens for light mode. See [globals.css](../frontend/src/app/globals.css).
- The diamond effect comes from geometry and directional light. Place a diagonal highlight on the upper-left of a dark surface, add a one-pixel edge and an inset top glint, then let the lower-right recede into black. Use the existing CSS polygon for chamfers. The button cuts are 10px, the reusable facet defaults to 16px and the evidence capsule uses 20px. An ordinary data panel can stay nearly square with the existing 2px radius. For a chamfered piece that needs a shadow, put the shadow on an unclipped wrapper. Keep focus visible outside clipped decoration.
- Use signal red (#e5342f) sparingly for the hero word, brand period and tiny signals. Status uses its own muted tones with a text label. Amber marks a wait, green marks a completed PR, coral marks failure, and neutral grey recedes. The chat's red pause indicator also carries explicit decision text. Primary actions use inverted monochrome, including in light mode.
- Geist Sans carries headlines and body copy. Use tight tracking and compact line height for display text, then restore breathing room in the body. Geist Mono gives stage labels, counters, repo names, hashes and uppercase section markers a consistent rhythm. Keep badges small and pill-shaped, navigation rows straight and icons thin. Use the real [Brand](../frontend/src/components/Brand.tsx) and existing Lucide icons.

## Match the five reference screens

### 1. Approval conversation

[GateConversation](../frontend/src/components/GateConversation.tsx) is a review panel inside the repair workspace. Give it a strong horizontal header, a diagonal glass face with a clipped top-right facet, and a rotated square around the upright message icon. Pair a mono gate label with “A second look, together.” Put “Awaiting your decision” at the far edge on wide screens. Separate the header, bounded conversation log and dark inset composer with thin rules. Center the empty-state icon and copy. Agent replies read as plain text with a small role label. User messages sit to the right on a stronger glass layer with one clipped corner. Keep the segmented Ask/Request controls and the 44px faceted arrow send button. Preserve the separate intent, pending and retry behavior, live announcements, local scroll, and the plan versus fix approval boundary. A question leaves approval pending, while a requested revision returns through the appropriate review flow.

### 2. Landing hero and calls to action

[LandingExperience](../frontend/src/components/LandingExperience.tsx) starts with the approval badge, then sets “Ship the fix.” in white and “Keep the veto.” in grey with only “veto.” in red. The supporting line tells the repair story in order. The mono line “Your repository. A tested patch. Your final say.” gives the user the last word. The white primary and glass secondary share the same silhouette. Reuse `.button`, `.button.primary` and `.hero-cta`. Default buttons are 48px minimum and hero CTAs 56px minimum. Their hover combines a 1px lift with a clipped sheen and their press scales to .98. Keep action labels verb-led and specific to the current state. Signed-in and signed-out routes use different labels and destinations.

The depth behind the hero comes from thin SVG curves and a faint red bloom near the window. Inside the tilted window, hairlines, a compact sidebar and a mono diff suggest real workspace proportions. Preserve “Illustrative walkthrough” and the example's evidence labels. Let the product window sit low enough to lead the eye into the next section. Keep text readable over the linework.

### 3. Task header, pipeline and PR evidence

The [task workspace](../frontend/src/app/tasks/%5Bid%5D/workspace.tsx) groups repo/source, status, large issue title and one prominent current action. The counters form a quiet mono strip below a divider. Below the header, the narrower left rail shows the actual stage sequence and the wider right side shows Plan, Evidence, Diff, Tests, Pull request and Events. Use the [StageStepper](../frontend/src/components/StageStepper.tsx) ordered list with numbered square nodes joined by a hairline. Completed steps get white checks, the current one gets a brighter edge and upcoming ones stay dim. Keep `aria-current` and failure text meaningful. Active tab emphasis is a thin underline. Allow long titles and counters to wrap before they crowd the action.

The PR panel uses a restrained nested surface with a green check, PR number, mono branch and URL, and a clear GitHub action. Use [StatusBadge](../frontend/src/components/StatusBadge.tsx) and [ActionButtons](../frontend/src/components/ActionButtons.tsx) for state-driven labels and availability. Keep Open/View PR distinct from Approve & create draft PR. Approval requires the exact diff hash. Rendering a completed stage, preview, or success badge must never trigger publication.

### 4. Navigation, sidebar and legal layout

[AppShell](../frontend/src/components/AppShell.tsx) anchors the workspace with the brand and wide white New Repair action. Active navigation uses a raised neutral row with stronger text and icon contrast. Keep Dashboard, Repair Tasks, Repositories and Settings in their established order. Preserve collapse/mobile drawer behavior, keyboard access, and the utility bar's breadcrumb, command search, API status, theme and profile controls. Public [Navbar](../frontend/src/components/Navbar.tsx) instead uses a quiet horizontal brand, section links and account-dependent actions. Keep the footer's thin divider, brand, simple links and mono signoff.

[LegalPage](../frontend/src/components/LegalPage.tsx) shares that public frame and uses a slim table of contents alongside a wider reading column. Use numbered mono markers, distinct headings, comfortable prose leading and hairline separation. The sparse layout gives policy copy priority. Preserve anchored sections, draft language and cross-links.

### 5. Change Capsule

[EvidenceLens](../frontend/src/components/EvidenceLens.tsx) pairs an editorial heading (“No black box. No crossed fingers.”) with an interactive evidence object. The left column has Source, Proof and Permission controls in a straight row. The right column has a chamfered face, top-lit edge, faint orbit lines and a diamond shield seal. The indexed mono header and footer rules frame one idea at a time. Use the existing panel swap and spring tilt. Keep the ornament behind the text and the reading order intact when the columns stack. Write evidence copy as a concrete claim with an inspectable object such as a pinned revision, test result or approval.

## Use the installed tools in their existing roles

- **UI/UX Pro Max** at `.agents/skills/ui-ux-pro-max/SKILL.md` is for checking keyboard access, focus, contrast, target size, responsive layout and motion. Apply its findings to this design system. Treat generated palettes and component suggestions as reference material.
- **Lenis** is installed and imported from `lenis/react`. [SmoothScroll](../frontend/src/components/SmoothScroll.tsx) currently uses the root with `lerp: .12`, anchor offset `-96` and nested scroll allowed. [Providers](../frontend/src/components/Providers.tsx) wraps the app with it. Keep nested chat, diff, log and modal scrolling independent via existing `data-lenis-prevent` boundaries. Preserve reduced-motion bypass and anchor offsets. Treat any move to route-specific scrolling as a separate change.
- **Motion** is installed and imported from `motion/react`. Providers uses `MotionConfig reducedMotion="user"`. Reuse opacity/transform reveals, `AnimatePresence` for panel and chat updates and `useMotionValue` plus `useSpring` for pointer tilt. The product window uses stiffness 120 and damping 20 and limits pointer tracking to mouse input. Reset tilt on pointer leave. Check reduced motion on both 3D surfaces and local chat scrolling. Keep visual effects off the task's critical path. `npx motion-ai` is unnecessary for existing runtime behavior.

## Check a frontend fix

Reproduce the issue in its real route and state. Identify the owning component, then reuse the nearest primitive or token and make the smallest change. Review the affected state against the matching screenshot and its neighboring states. Test dark and light, narrow and wide layouts, keyboard focus, reduced motion, and relevant loading/error/empty states. Check nested scroll and mobile overflow where applicable. Use the existing lint/build scripts when permitted by repository policy and state what you verified. Preserve task/API behavior, GitHub access and human approval gates.
