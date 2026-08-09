# Decision: docs website Next.js version (P6.3)

**Date:** 2026-07-10
**Scope:** the marketing/docs site at `website/` (deployed to openafon.vercel.app via Vercel CLI;
the dir is gitignored — NOT part of the OpenAfon runtime). This does not touch the Python assistant.

## Decision

**Stay on Next 14 — bumped 14.2.15 → 14.2.35 (done). Do NOT upgrade to Next 16 / React 19 now.**

## Why

`npm audit` flagged 1 **critical** + advisories on the old `next@14.2.15`. Two facts drive the call:

1. **The 14.2.35 patch (zero code changes) cleared the CRITICAL.** After the in-line bump, `npm audit`
   drops to `1 high, 1 moderate` — no critical. This is a one-line `package.json` change + reinstall,
   no React 19, no breaking changes.

2. **The remaining high/moderate advisories are feature-gated to capabilities this site does not use.**
   `npm audit` is feature-blind: it lists every CVE for the installed Next version regardless of whether
   the app exercises the vulnerable path. This site is **static docs** — verified: no `middleware.ts`,
   no `use server` / server actions, no `rewrites`, no `remotePatterns` on the image optimizer, no CSP
   nonces, no auth, no user input. So:

   | Remaining advisory | Applies here? |
   |---|---|
   | Middleware auth-bypass / SSRF / redirect cache-poison | **No** — no middleware |
   | Server-Components / Server-Actions DoS & deserialization | **No** — no server actions, static render |
   | HTTP smuggling in `rewrites` | **No** — no rewrites |
   | Image Optimizer `remotePatterns` DoS | **No** — local images only, no remotePatterns |
   | XSS via CSP nonces / `beforeInteractive` untrusted input | **No** — no CSP nonces, no untrusted script input |
   | Image Optimization API DoS / disk-cache growth | **Partial** — but Vercel runs & patches the optimizer; images are static |

   The only partially-applicable class (image optimization) is mitigated by Vercel's managed platform.

3. **Next 16 + React 19 is a breaking major upgrade** (React 19 codemods, RSC/caching semantics changes,
   possible component breakage) with **near-zero real security benefit** for a static docs site once
   14.2.35 is in. The regression risk outweighs the upside right now.

## What was done

- `website/package.json`: `next` `14.2.15` → `14.2.35` (reinstalled; critical cleared).
- **Not yet redeployed** — takes effect on the next `vercel --cwd website --prod`. Outward-facing, so
  left for an explicit go-ahead.

## Revisit Next 16 when

- the site gains dynamic features (auth, forms, server actions, middleware), OR
- Next 14.x reaches end-of-life, OR
- an advisory lands that actually applies to a static site.

At that point: run the React 19 codemod, upgrade in a branch, `npm run build` + visual QA, then redeploy.
