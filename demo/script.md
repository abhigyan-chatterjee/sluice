# Sluice demo — under 90 seconds

Recorded against the deterministic fixture server so no beat depends on a live vendor
behaving. Optional real-site beat at the end, only if it behaves on the first take.

Setup before recording (off camera):

```sh
python demo/fixture_server.py --port 8765 &
hermes sluice doctor > /dev/null        # warm the read path
```

Tab 1: a terminal at the repo root. Tab 2: Hermes Desktop with the Sluice pane open.

## Beats

**0:00–0:12 — the inventory.**
`hermes sluice doctor`
Read the header: twelve backends, tier order, keyless on. Point at one `local`, one
`keyless`, one `free` and one `paid` row, then at a `missing:` line and the `next:`
command under it.
Say: "one plugin, twelve backends, free first — and every gap prints the command that
closes it."

**0:12–0:28 — a live query names its backend.**
`hermes sluice test --backend ddgs "zstd vs gzip compression ratio"`
Show the result line (`ddgs (keyless, keyless) answered in … ms`) and two rows.
Say: "the answer carries the backend and the tier that served it."

**0:28–0:44 — fallback in one request.**
Park the top backend the way a real quota error would:

```sh
python -c "from sluice.config import plugin_data_dir; from sluice.store import open_store; open_store(plugin_data_dir()).note_quota('ddgs')"
hermes sluice status
```

Point at `ddgs  quota` in the health column, then type the same query in the pane's test
box. The result line names a different backend.
Say: "a quota error parks that backend for an hour; the next request lands on the next rung
without a retry."

**0:44–1:05 — the challenged page.**
`python -c "from sluice.ladder import fetch; r = fetch('http://127.0.0.1:8765/cf'); print(r['block_class'], r['stage']); print(r['content'][:100])"`
Show `challenge chromium` and the provenance line at the top of `content`.
Switch to the pane's block report: `127.0.0.1  challenge  via chromium`.
Say: "the classifier names the vendor, the ladder climbs to a real browser, and the block
report remembers which rung worked for 24 hours."

**1:05–1:20 — policy first.**
`python -c "from sluice.ladder import fetch; r = fetch('http://127.0.0.1:8765/private'); print(r['block_class'], r['blocked_by_policy'], r['error'])"`
Show `policy_blocked True` and the quoted robots rule.
Say: "robots.txt is checked before the first request; a disallow never reaches the network."

**1:20–1:25 — close on the tier list.**
Back to `hermes sluice doctor`, hold on the table.
Say: "local, keyless, free, paid — in that order, always."

After the take, clear the demo's parking spot:

```sh
python -c "from sluice.config import plugin_data_dir; from sluice.store import open_store; open_store(plugin_data_dir()).record_success('ddgs')"
```

## If a beat misbehaves

- Pane empty: the dashboard API mounts at `/api/plugins/sluice/` after the gateway restarts;
  restart Hermes and reopen the pane.
- `/cf` comes back `js_required` instead of `challenge`: `curl -i http://127.0.0.1:8765/cf`
  shows the `cf-mitigated: challenge` header the classifier keys on; the fixture is correct,
  the recording box may be rewriting headers.
- Playwright not installed: the ladder stops at stage 1, the provenance line says
  `status=challenge`, and the policy beat plus the tier list still carry the demo.
