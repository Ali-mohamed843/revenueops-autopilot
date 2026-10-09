# RevenueOps dashboard

Next.js 15 (App Router) + Tailwind 4 over the agent service API. See the
[project README](../README.md) for what each page does.

```bash
cp .env.local.example .env.local   # AGENT_API_URL, ADMIN_API_KEY
npm install
npm run dev -- --port 3001
```

## How it's built

- **Server-first.** Pages are server components that call the agent service
  (`src/lib/api.ts`, marked `server-only`). Buttons call server actions
  (`src/app/actions.ts`), which add the admin key, so it never reaches the
  browser.
- **Design** follows the "RevenueOps 2060" Claude Design canvas. Tokens live in
  `src/app/globals.css` as CSS variables for light and dark; components use
  roles (`bg-card`, `text-ink-2`, `text-warn`), never hex. Fonts are self-hosted
  with `next/font` (Newsreader, Geist, Geist Mono, IBM Plex Sans Arabic).
- **Charts** (`src/components/charts.tsx`) are small SVG components: donuts whose
  legends carry the exact values, and a line chart with a hover/keyboard
  readout and a screen-reader table.
- **Status** is never colour alone: pills pair a tone with an icon and a label.

## Checks

```bash
npm run lint
npm run typecheck
npm test        # unit tests (Vitest)
npm run build
```
