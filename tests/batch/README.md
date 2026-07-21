# Batch capability testing (probe suite)

Purpose: find this agent's **ability boundaries** and build a **regression baseline** by
running a curated list of tasks and scoring pass/fail + latency + record-count.

This is the concrete form of todo.txt #3 ("I don't know the ability boundaries now →
test to find out"). It is DEPLOYMENT-independent: it measures *coverage*, not *concurrency*.

---

## 1. No Docker → run SEQUENTIALLY

Docker only buys **concurrency** (N users' browsers at once). Measuring ability boundaries
needs **coverage**, not concurrency.

- **Lost without Docker:** running probes in parallel. Can't test "3 users at once."
- **Kept:** running probes **one after another** in one process, reusing one `keep_alive`
  browser session. Each probe gets its own `request_id`, Langfuse trace, and pass/fail
  + latency record. That sequence IS the capability map.
- Concurrency (todo M-5) is a **separate, later axis** — deferred until Docker works.

**Order dependence (feature, not bug):** sequential probes share one warm profile. A probe
that logs into site A leaves that session warm for later probes on A. Record which probes
assume a prior login (see `depends_login` in `probes.jsonl`).

---

## 2. Login handoff during batch → reuse the Telegram callback

Login handoff is a **callback**, not hardwired to Telegram. The runner calls
`on_login_required(url, agent)` whenever `core/handoff.py` detects a login wall. The
Telegram gateway *supplies* that callback (`telegram_gateway._login_required`), which:

1. Starts a noVNC / tunnel remote view of the agent's browser.
2. `bot.send_message(chat_id, "🔐 Login needed… Open: <link>… Reply /done")`.
3. Agent pauses until you send `/done`, which fires the resume signal.

**Decision: run the batch INSIDE the Telegram gateway (a `/probe` command), NOT as a
standalone script.** Reasons:

- The gateway already owns `on_login_required` → the batch **inherits real Telegram
  handoff for free**. A login wall in any probe messages you the noVNC link; `/done`
  resumes; batch continues. Zero new handoff plumbing.
- A standalone script would have to re-create a Telegram client + RemoteView/Tunnel
  wiring — duplicating the gateway. Not worth it.

**Why sequential makes handoff simple:** the `/done` resume is a single shared signal.
Sequential ⇒ only one login wall pending at a time ⇒ one `/done` is unambiguous. Concurrency
would break this (two walls, one `/done`) — another reason concurrency is deferred.

---

## 3. Use-case families (product view)

Positioning (todo L194): *access info that needs login / is personal, in batch, to make
better decisions.*

| Family | Example | Note |
|---|---|---|
| A. Batch harvest of login-gated public content | 面经 (小红书), Glassdoor, LinkedIn posts | today's proven path |
| B. Your own authenticated/personal data | "summarize my last 20 Gmail threads about X", "my recent Amazon orders" | safest ToS-wise; #1 wedge |
| C. Cross-source aggregation for a decision | "compare offer discussions for X across 小红书 + 一亩三分地 + LinkedIn" | the "better decisions" promise |
| D. Personalized monitoring / recurring | "new 面经 since last run" (ledger resume) | recurring value |
| E. Authenticated actions (not just read) | fill a form, apply, send a message | highest value + risk |

---

## 4. Ability-boundary dimensions (where it breaks)

1. Login handoff — detect wall, request login, resume (cold vs warm)
2. Search reliability — direct-URL search vs typing-in-a-box
3. Extraction fidelity — structured records vs junk feed; dedup correctness
4. Long-horizon stamina — 60+ items across chunks without stalling
5. Relevance judgment — agent-judged, no hardcoded tokens
6. Novel-site generalization — a site with no skill file
7. Anti-bot / rate-limit — degrade gracefully vs loop-and-burn
8. Latency / cost per result — Langfuse ~24s/step, per task type

---

## 5. Probe suite (see `probes.jsonl`)

| # | Task | Site | Dims | Pass criterion |
|---|---|---|---|---|
| P1 | Harvest 20 面经 for a listed company | 小红书 | 1,3,4 | ≥18 deduped records w/ url+body |
| P2 | Same for an **unlisted** company (ad-hoc) | 小红书 | 5,6 | router→harvest, ≥15 relevant |
| P3 | Harvest from a **second source** | 一亩三分地 | 6 | ≥10 records, no login-loop |
| P4 | "Summarize my last 10 emails about offers" | Gmail | 1,2 | login handoff fires; 10 summarized |
| P5 | Browse task, no harvest ("what's trending on X") | any | router→None | routes to run_task |
| P6 | Cold session (logged out) harvest | 小红书 | 1 | detects wall → request_login → resumes |
| P7 | Warm-session repeat of P1 | 小红书 | 4 | resumes via ledger, few new records |
| P8 | Cross-source compare (2 sites, 1 goal) | 2 sites | 3,C | records from both, attributed |
| P9 | Deliberately vague goal | any | 5 | clarifies or picks sensibly, no crash |
| P10 | Rate-limit-prone rapid harvest | 小红书 | 7 | backs off, no infinite loop |
| P11 | Novel site never seen | new | 6 | partial success OR clean "can't" |
| P12 | Action task (fill a safe form) | safe form | 8,E | completes or stops at a gate |

**Tiers:** tier 1 = proven (小红书 + router: P1, P2, P5, P6, P7). Run tier 1 FIRST so a
login/anti-bot snag doesn't poison the whole batch. Tiers 2–3 add new sites / actions to
actually find the boundary.

---

## 6. Runner shape (to build)

`/probe [tier]` command inside `TelegramGateway`:

1. Load `tests/batch/probes.jsonl` (optionally filter by tier).
2. Set `_busy` for the whole batch.
3. Loop each probe: `new_request_id()` → `route_request` → `run_task` / `run_harvest`.
4. Login walls → existing `_login_required` messages you; `/done` resumes.
5. Report start/finish + pass/fail/#records/latency per probe (reuse `_progress`).
6. Write `workspace/probes/<ts>/results.jsonl` + summary markdown.

Each result row: `request_id, probe_id, goal, pass, records, steps, wall_ms, trace_url,
error`. That table = the ability boundary + regression baseline.

Status: **design only. Not yet implemented.**
