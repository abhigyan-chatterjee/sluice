# Typeform submission — the real form

Found 2026-11-10. Source: the official Nous Research announcement and the live form itself.

**Form:** https://form.typeform.com/to/FwJLvOln
**Title:** "Hermes Agent Plugin Hackathon Submission Form"
**Status:** live, HTTP 200, verified by fetching its public schema.

## The seven fields, in order

Read from `api.typeform.com/forms/FwJLvOln`. Field order is the order the form asks them.

| # | Field | Type | Required | What to put |
|---|---|---|---|---|
| 1 | Plugin Title | short text | yes | `Sluice` |
| 2 | Plugin Description | short text | yes | The one-liner from `plugin.yaml:3` |
| 3 | Email Address | email | yes | `abhi@nullbit.in` |
| 4 | Discord Username | short text | yes | **Abhi must supply** — not recorded anywhere |
| 5 | X Demo Post Link | website | **yes** | The link to the demo video **posted on X** |
| 6 | *(statement)* | — | — | Instructions to open the catalog PR, then Continue |
| 7 | GitHub Link to your Hermes Plugin Pull Request | website | yes | https://github.com/NousResearch/hermes-agent/pull/152754 |

## Field 5 is the one that bites

`X Demo Post Link` is a required website field. It is **not** a Loom, YouTube or Drive link —
it is the URL of a post on X that carries the demo video and tags @NousResearch.

The announcement states the entry requirements as three steps:

1. Open a pull request that adds the plugin to the Hermes plugin catalog.
2. **Post a demo video on X and tag @NousResearch.**
3. Submit both links through the form.

So the video has to be published on X first, then that post's URL goes into field 5. A video
sitting on disk, or on Drive, or on unlisted YouTube, does not satisfy this field.

This was never in `SUBMISSION.md` §3, which said "upload to Loom, YouTube (unlisted), or Google
Drive". That instruction is wrong for this form.

## Deadline

Thu 12 Nov 2026, 03:30 IST (11 Nov 17:00 EST). The PR may go in before the form; catalog
checks run when a maintainer starts them.

## What this means for the last few hours

- The video is done and verified: `/home/abhi/sluice-demo-render/sluice-demo.mp4`, 56.9 s.
- It still has to be posted on X with @NousResearch tagged, and that post's URL captured.
- The Discord username is a field nobody has filled in.
- Everything else is ready: PR #152754 is open, the pin is `b40e6b9`, the entry validates.
