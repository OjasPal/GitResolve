# Frontend UI fixes handoff

## Branch and working state
- Current checkout C:/Users/Ojas Pal/GitResolve is on frontend/ui-fixes at faa0c7c.
- Earlier isolated worktree was removed by the user.
- Earlier frontend milestone and both imported main authorization commits are committed.
- Current changes remain uncommitted.
- Removed the private-repository documentation link from shared LegalPage, covering Privacy, Terms and Your data.
- Removed the duplicate permission grid from Settings GitHub access.
- Removed the display-name helper sentence, enlarged its label and refined the input using existing theme tokens.
- Save appears only for a changed name, disables during saving/empty input, and disappears after a successful save.
- No new dependencies or backend changes in this turn.

## Validation
- Hardened network-disabled read-only node:22-alpine containers, nonroot user, no credentials/socket.
- node node_modules/typescript/bin/tsc --noEmit --incremental false -> passed.
- node node_modules/eslint/bin/eslint.js src/app/settings/page.tsx src/components/LegalPage.tsx -> passed.
- git diff --check -> passed.
- Browser appearance and authenticated name saves were not live-tested.

## Prior functionality
- Help at /help shares the policy reading layout.
- Footer begins Help, Privacy, Terms, Your data. GitHub follows FAQ in the header.
- Approval chats have one Ask a question composer.
- GitHub popup has two setup steps.
- Delete history removes finished attempts from visible history using retention-preserving tombstones; active tasks stay visible.
- Earlier history/API security/lifecycle checks passed 41 tests.

## Remaining work
- Browser review of the latest Settings refinements.
- Verify Firebase display-name persistence after GitHub reauthentication.
- Commit current edits when approved.
