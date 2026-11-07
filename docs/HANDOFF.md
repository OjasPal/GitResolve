# Frontend UI fixes handoff

## Branch and working state
- Branch is frontend/ui-fixes, based on 2094fe8.
- Worktree is C:/Users/Ojas Pal/.codex/visualizations/2026/11/07/01a1b360-f75f-7a63-a2f8-c23fbe0eebe1/frontend-ui-fixes.
- Antigravity continues in the original checkout on agent/workflow-optimization.
- Changes remain uncommitted.
- Removed duplicate inline legal navigation and Product/Safety/FAQ footer links.
- Settings supports Firebase display-name edits with validation and immediate context updates.
- Approval gates now have a plain Ask a question label and one question composer.
- Help content was approved and implemented at /help using LegalPage's shared layout.
- LegalPage accepts structured content and guide mode. Policy pages retain their existing draft notice and documentation link.
- Help uses anchored numbered sections, ordered steps, navigation links and readable troubleshooting definitions.
- Public footer order is Help, Privacy, Terms, Your data.
- GitHub moved after FAQ in public desktop and mobile navigation.

## Validation
- Checks ran in network-disabled read-only node:22-alpine containers as user node.
- Capabilities dropped, no-new-privileges, 768 MB RAM, 2 CPUs, 100 PID limit.
- Source/config/dependencies mounted read-only with no credentials or Docker socket.
- node node_modules/typescript/bin/tsc --noEmit --incremental false passed.
- node node_modules/eslint/bin/eslint.js src/app/help/page.tsx src/components/LegalPage.tsx src/components/AppShell.tsx passed.
- Prior settings/auth/chat targeted lint passed.
- git diff --check passed.
- No live model calls. No browser visual or authenticated Firebase checks performed.

## Remaining work
- Review /help in a running browser at desktop and mobile sizes.
- Verify custom display-name persistence through GitHub reauthentication.
- Coordinate integration with Antigravity before moving these changes to its checkout.
- Original checkout contains the earlier untracked premium implementation guide.
