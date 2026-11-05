# Sluice

Sluice is one plugin that owns Hermes' web layer. It serves the native `web_search`
and `web_extract` tools through a single routing provider, and it manages every web
backend a user has: local, keyless, free-plan and paid.

Routing is free-first: self-hosted, then keyless public tiers, then free-plan keys, then
paid keys. Paid is last by default, never exclusive. Extract is local only: honest
`httpx` plus trafilatura, then real Chromium through Playwright, then the Camofox REST
server, with a deterministic block classifier deciding when to climb and when to stop.

**Status: scaffold.** This repository is task T1 of the build plan: manifest, packaging,
module layout, CLI parser and smoke tests. Routing (T3), the store (T2), the adapters
(T4-T6), the fetch ladder (T7), the manager (T8), the dashboard (T10) and the desktop pane
(T11) land as their own tasks. Until the router exists `is_available()` reports `False`,
so installing the scaffold never advertises a capability it does not have.

- `PLAN.md` — the build plan and acceptance criteria, task by task.
- `DESIGN.md` — the locked design (architecture, tier policy, extract chain, non-goals).
- `REQUIREMENTS.md` — the traceable requirements extracted from the plan.
- `references/endpoints.md` — exact request and response shapes for all twelve vendors.

## Development

```sh
uv venv
uv pip install -e ".[dev]"
pytest
```

Hermes plugin checks, from the repository root:

```sh
hermes plugins doctor .
hermes plugins validate .
```

## License

MIT. See `LICENSE`.
