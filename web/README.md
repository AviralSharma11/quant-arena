# `web` — the frontend

Task 5.4a: the scaffold and routing. The screens themselves arrive later — 5.4b builds auth
(week 2), 6.1 the trading screen (weeks 5–6), 7.2 the backtest screen (week 7).

## Running it

```bash
docker compose up -d          # the gateway, from the repository root
cd web && npm install         # first time only
npm run dev                   # http://localhost:5173
```

`vite.config.ts` proxies `/auth`, `/orders`, `/stream` and the rest to the gateway on port 8000,
so the browser sees a **single origin**. That is what lets the gateway's session cookie work
untouched: Open Issue 015 requires `httpOnly` / `Secure` / `SameSite`, and a cross-origin dev
setup would force a weaker cookie in development than the one used in production — which is how
an auth bug survives until deployment.

```bash
npm run build     # tsc -b && vite build
npm run preview   # serve the built app
npm run lint
```

## Three screens, and only three

`src/routes.ts` holds the route table as plain data, and `App.tsx` builds the router from it.
Open Issue 014 sub-decision 14d fixes the list at three — trading, auth, backtest — and §11.1
records why: interface work has no natural stopping point, so the limit is a commitment made in
advance rather than a judgement made later under pressure.

A fourth screen is a scope change to be raised, not an entry to add to the array.
`tests/web/test_scaffold.py::test_there_are_exactly_three_screens` is where that stops being a
sentence in a document.

## Testing

```bash
.venv/bin/python -m pytest tests/web -q     # from the repository root
```

There is no JavaScript test framework, deliberately — the stack list is closed. Node 24 strips
TypeScript natively, so the tests import `routes.ts` directly to inspect the route table, and
serve the built app over HTTP to check it responds on every path.

## Not here yet

No WebSocket client, no gap detection, no `requestAnimationFrame` loop — all 5.4c, week 4. When
they arrive, high-frequency data goes in a mutable buffer **outside React state**, painted by a
frame loop, because a framework re-rendering on every 20 Hz book update drops frames.
