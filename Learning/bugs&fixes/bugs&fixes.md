# Bugs & Fixes

A running log of real bugs hit in this project, their root cause, and the fix.
Newest at the top.

---

## Bug: "Task succeeded" but Telegram returned "没有采集到可用内容，只有占位符"

**Date:** 2026-07-13

### Symptom
- The agent finished a run and its `done` text looked correct (real title/URL/body of
  the user's latest 小红书 post).
- But the Telegram reply was the **synthesizer** output:
  `目前没有采集到可用的小红书帖子内容；现有记录只有占位符...`
- Langfuse showed the run "succeeded" (8 generations, `done` with `success=True`).

### Root cause
Two-tool handoff failure between `run_js` and `save_items`.

The agent extracted the post via `run_js` (result visible in its own context), then
called `save_items` with **placeholder tokens** instead of the real values:

```json
[{"title":"__FROM_RUN_JS__","url":"__FROM_RUN_JS__","body":"__FROM_RUN_JS__"}]
```

The model assumed the harness would backfill `__FROM_RUN_JS__` from the previous
`run_js` result. Nothing did, so the stored record was literally those placeholder
strings.

The disconnect:
- **Agent `done` text** = real content (agent saw the `run_js` output in-context).
- **Saved record** = placeholders.
- **Synthesizer** only reads *saved records* → sees placeholders → correctly reports
  "no usable content."
- Telegram shows the synthesizer output → the user sees failure even though `done`
  claimed success.

Lesson: a run that saves empty/placeholder records must FAIL LOUDLY, not silently
report success. Never trust the agent's `done` text as proof data was captured — the
downstream (synthesizer) consumes the *store*, so validate the store.

### Fix
`src/browseruse_bot/core/tools.py` + `skills/xiaohongshu-search.md`:

1. **New `extract_and_save` tool** — the JS snippet RETURNS the record and it is saved
   directly in one step. No re-typing → placeholders are structurally impossible. Also
   removes a round-trip (minor latency win).
2. **Hardened `save_items`** — backfills any `__FROM_RUN_JS__` from the last `run_js`
   result (`_resolve_sentinels`); if placeholders still remain, it **rejects the save
   with an error** instead of storing junk.
3. **Skill updated** — per-post extraction uses `extract_and_save`; explicitly forbids
   placeholder strings.

Net: a run can no longer "succeed" while saving empty records — either real content is
stored, or it fails loudly and the agent retries.

---

## Observations: engineering problems surfaced by the same run (latency + routing)

Data came from the Langfuse trace of the run (per-step latency/tokens/cost) plus
`workspace/logs/bot.log`.

Trace summary: wall 514.6s, LLM 228.4s (44%), non-LLM browser overhead 286s (56%),
87,444 in / 3,827 out tokens, $0.276 — for one simple "get my latest post" task.

### The three real problems

1. **`gpt-5.4` is the latency tax** — every step is 19–47s. This is the same slow
   reasoning model from before. Swapping the *agent* to `gpt-4.1` cuts per-step latency
   ~3–5x (≈228s → ~60s) and eliminates the JSON-parse failures. Biggest single win.
2. **~56% of wall time is non-LLM overhead — but it's a *mix*, not all screenshots.**
   The **15s** stall in the log was a one-off `ScreenshotWatchdog` **timeout** (that
   step's screenshot got stuck mid page-load; the cap is 15s), NOT a per-step cost.
   Normal capture is sub-second. The 56% is really: that one 15s stall + 3s
   page-readiness waits + navigation + CDP round-trips + `run_js` execution. So
   disabling screenshots helps but does not "reclaim 56%" — earlier claim overstated.

   Key insight: **`use_vision=False` does NOT stop screenshot *capture*.** It only
   (a) excludes the image from the LLM messages and (b) removes the `screenshot`
   tool. browser-use's step loop **hardcodes `include_screenshot=True` every step**
   (`agent/service.py` ~L1089, comment: "always capture even if use_vision=False so
   that cloud sync is useful") — kept only for browser-use Cloud sync / run GIFs.
   Coordinate conversion uses `page_info`, not the screenshot (`dom_watchdog.py`
   ~L497), so skipping capture with vision off is safe; you only lose debug
   screenshots / the run GIF (fine — we have Langfuse + transcripts).

   **Fix applied** (`core/runner.py`): when `use_vision` is off, `_disable_screenshots`
   wraps `session.get_browser_state_summary` to force `include_screenshot=False`, so
   no `ScreenshotEvent` is ever dispatched. BrowserSession is a pydantic model with
   `validate_assignment`, so the wrapper is installed via `object.__setattr__` (the
   instance attr shadows the class method since functions are non-data descriptors).
3. **Skill mis-routing (correctness noise).** Your task was "get my latest post," but
   `/new` auto-prepended the **`Xiaohongshu Search`** skill ("harvest ~15 posts, don't
   summarize"). It still worked, but injected conflicting instructions and forced a
   synthesizer call over a single record. The skill matcher fires on "小红书" alone —
   too broad.

### Status
- [ ] (1) Switch agent model to `gpt-4.1` (keep tracing to measure before/after).
- [x] (2) Skip screenshot capture when `USE_VISION=false` (`_disable_screenshots` in
  `core/runner.py`). Removes the always-on capture + the 15s stall risk.
- [x] (3) Tighten skill matching: added `Intent-Triggers` to the skill format
  (`core/skills.py`); a bare site name like "小红书" no longer auto-applies a skill.
  Company 面经 requests now route to the dedicated harvester before skill matching.
