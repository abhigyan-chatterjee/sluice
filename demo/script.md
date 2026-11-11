# Sluice demo — under 90 seconds

The film is recorded by a VHS tape, so every frame is the terminal's own output: real
colour, real wrapping, real timing. Nothing is a rendered slide. The tape is
`sluice-demo-render/sluice.tape` and the film it produces is 56.9 s at 1920x1080.

Recorded against the deterministic fixture server, so no beat depends on a live vendor
behaving.

Setup before recording (off camera):

```sh
python demo/fixture_server.py --port 8765 &
hermes sluice-search doctor > /dev/null        # warm the read path
```

## Beats

**1 — the inventory.**
`hermes sluice-search doctor`
Twelve backends, tier order, keyless on, and a `next:` command under every gap.

**2 — a real search names its backend.**
`hermes sluice-search test --backend ddgs 'python asyncio tutorial'`
The result line carries the backend, the tier and the latency it took.

**3 — the same query on a different tier.**
`hermes sluice-search test --backend exa 'python asyncio tutorial'`
Identical contract, different backend: the caller does not care which rung answered.

**4 — persisted health, latency and spend.**
`hermes sluice-search status`
Per-backend health, p50 and EMA latency, call counts and spend, read back from the store
rather than held in memory.

**5 — a bot challenge, classified and climbed.**
`~/sluice-demo-render/ladder.sh http://127.0.0.1:8765/cf`
`block_class: challenge`, `stage: chromium`, and the per-stage provenance underneath.

**6 — policy first.**
`~/sluice-demo-render/ladder.sh http://127.0.0.1:8765/private`
`policy_blocked`, and the refusal quotes the robots rule it obeyed.

**7 — the suite the submission cites.**
`cd ~/sluice && .venv/bin/python -m pytest -q`

## Re-proving the film

`bash sluice-demo-render/verify.sh` re-runs every claim in the film against the live system
and prints PASS or FAIL per claim. Run it before submitting.

## If a beat misbehaves

- `/cf` comes back `js_required` instead of `challenge`: `curl -i http://127.0.0.1:8765/cf`
  shows the `cf-mitigated: challenge` header the classifier keys on; the fixture is correct,
  the recording box may be rewriting headers.
- Playwright not installed: the ladder stops at the HTTP stage and the provenance line says
  `status=challenge`. Run `hermes sluice-search install playwright` and re-record.
- ddgs answers `No results found`: it fans out across public engines and rate-limits
  intermittently. Retry the beat; it is upstream, not the plugin.
