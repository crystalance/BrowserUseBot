# Improving the browser-use agent — method & results

How we turned a slow, ineffective agent run ("40 steps, 1 post, fail") into a
fast, rich, reliable one ("collect 14 posts with full text → grounded summary").
This documents the *method* (how we diagnosed and iterated) and each change with
its rationale, so the approach is reusable for other tasks.

## The diagnostic loop (how we found what to fix)

Every change was driven by **evidence from run transcripts**, not guesswork:

1. **Per-run logging + transcripts.** `core/logging_setup.py` writes rotating
   logs; the runner saves a full per-run transcript to
   `workspace/logs/transcripts/run-*.json` (goal, ok, steps, model_output,
   results/errors).
2. **Transcript analysis scripts.**
   - `scripts/inspect_transcript.py` — per-step actions + timing (finds slow
     steps, loops, tab-switch churn).
   - Ad-hoc counters — action histogram, `json_errors`, `timeouts`, per-step
     error text (finds the *real* failure cause vs symptoms).
3. **Trajectory HTML** (`scripts/render_trajectory.py`) — a readable page of the
   whole run for eyeballing effectiveness.

This turned vague "it's not effective" into concrete metrics: *N steps, which
actions, how many wasted on errors/timeouts, how many results produced.*

## The problems we found (with evidence)

| Symptom | Root cause (from transcripts) |
|--------|-------------------------------|
| "no result" / task dies early | Azure content filter blocked screenshots; later gpt-5.4 emitted malformed JSON (`trailing characters`) every step |
| Agent wanders to Google | It abandoned the logged-in site; fixed with a **skill** |
| 24-step `find_elements` loop, no links | Result cards have no scrapable `href` in the expected format (`/search_result/<id>?xsec_token=`); one-element-at-a-time is the anti-pattern |
| 40 steps, 1 post, `ok=False` | ReAct does 1 LLM call per micro-action → open-each-post is ~3 steps/post; budget exhausted |
| Output = titles+links only | Agent never read the **main text (正文)** |
| ~45 min runtime | gpt-5.4 latency (40–85s/step) + six 75s LLM timeouts |

## The improvements (each with rationale)

### 1. Model reliability
- **gpt-5.4 malformed JSON** — browser-use uses *strict* structured output;
  gpt-5.x appends trailing tokens the parser rejects. Prompt tweaks don't fix it.
  - Azure gpt-5.x → force the **Responses API** (`use_responses_api=True` in
    `build_llm` for `gpt-5*`; needs api-version ≥ 2025-03-01-preview). Cut but
    didn't eliminate the errors.
  - **gpt-4.1** eliminates the JSON errors *and* the 75s timeouts — the real fix.
    (Azure gpt-4.1 deployment is currently 429 rate-limited → raise TPM quota.)
- **Vision off by default** (`USE_VISION=false`, `/vision on|off` at runtime) —
  DOM-only is enough for text tasks, cheaper/faster, and sidesteps Azure's
  screenshot content filter.

### 2. CodeAct: the agent writes code at runtime (`run_js`)
- New general action `run_js(code)` (`core/tools.py`) executes agent-authored
  JavaScript via CDP `Runtime.evaluate` and returns JSON.
- Instead of acting element-by-element (one LLM round-trip each), the agent
  writes **one** JS snippet to bulk-harvest `{title, href}` for all cards, or to
  read a post's full body. Replaced the 24-step `find_elements` loop with ~1 call.
- **The router is the agent itself** — the tool description + skill steer it to
  use `run_js` for bulk/repetitive DOM work and normal actions otherwise. No
  separate classifier.

### 3. Two-phase: separate browser automation from synthesis
- **Phase 1 — collector (browser-use):** gather raw data only. Generic
  `save_items(items_json)` action appends arbitrary records (any fields — posts,
  products, jobs…) to `workspace/collected/run-*.jsonl`. Batched.
- **Phase 2 — synthesizer (`core/synthesizer.py`):** a **browser-less LLM call**
  reads the collected records and organises the final answer, grounded only in
  the saved text, preserving links.
- Why: browser-use stops wasting steps on summarising; the sub-agent summarises
  *all* records at once, so the collection cap can rise (8 → ~15) without burning
  browser steps. Clean separation of concerns.

### 4. Same-tab overlay (no new tabs)
- The site opens a clicked card as an in-page **overlay**. Skill flow:
  `click card → run_js extract 正文 → save_items → Escape → next`. No `navigate`,
  no new tabs, no tab-switch churn. (Verified: `navigate ×1` for a 14-post run.)

### 5. Skills as reusable, steering prompts
- `skills/*.md` matched on `/new` (auto-router); the matched skill's instructions
  + host policy are injected. Encodes the *correct method* for a task class
  (stay on-site, harvest once, read overlays, save full body, don't summarise).

## Results (before → after)

| Metric | Before | After (2-phase, same-tab) |
|--------|--------|---------------------------|
| Posts collected | ~1–2 | **14**, with full 正文 |
| Output quality | titles + links | grouped, **body-grounded** summaries + links |
| Wasted steps | 24-step href loop | none (harvest once, overlay loop) |
| `ok` | False | **True** |
| Tabs opened | many (switch ×7) | 1 (`navigate ×1`) |

Remaining lever: **speed** — still ~45 min due to gpt-5.4 latency + 75s timeouts.
**gpt-4.1** (same architecture) removes the timeouts and ~halves per-step time →
~15–20 min. Blocked only on Azure gpt-4.1 TPM quota (429).

## Reusable takeaways
1. **Instrument first** — transcripts + per-step timing + error counts turn
   opinions into fixes.
2. **Fix the model, not the prompt**, for structured-output failures (Responses
   API / different model), and confirm with evidence.
3. **CodeAct via `run_js`** collapses repetitive DOM work into one agent decision.
4. **Split collect (browser) from synthesize (LLM)** — more results, cleaner code,
   scales the cap.
5. **Encode the method as a skill** so the agent follows the proven path and only
   uses heavy tools when appropriate.
