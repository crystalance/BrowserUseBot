"""Render a browser-use run transcript into a single self-contained HTML page."""

import html
import json
import sys
from pathlib import Path


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def action_summary(a: dict) -> str:
    if not a:
        return ""
    name = list(a.keys())[0]
    params = a[name] or {}
    if isinstance(params, dict):
        bits = []
        for k, v in params.items():
            s = str(v)
            if len(s) > 120:
                s = s[:120] + "…"
            bits.append(f"{esc(k)}=<span class='pv'>{esc(s)}</span>")
        inner = ", ".join(bits)
    else:
        inner = esc(params)
    return f"<span class='act'>{esc(name)}</span>({inner})"


def main() -> None:
    src = Path(sys.argv[1])
    out = Path(sys.argv[2])
    d = json.loads(src.read_text(encoding="utf-8"))
    steps = d["history"]["history"]

    rows = []
    for i, s in enumerate(steps, 1):
        mo = s.get("model_output") or {}
        ev = mo.get("evaluation_previous_goal") or ""
        mem = mo.get("memory") or ""
        goal = mo.get("next_goal") or ""
        acts = [action_summary(a) for a in (mo.get("action") or []) if a]
        results = []
        for r in s.get("result") or []:
            if not isinstance(r, dict):
                continue
            if r.get("error"):
                results.append(f"<div class='err'>❌ {esc(r['error'][:400])}</div>")
            if r.get("extracted_content"):
                c = r["extracted_content"]
                if len(c) > 700:
                    c = c[:700] + "…"
                results.append(f"<div class='ok'>📄 {esc(c)}</div>")
        verdict = "err" if any("err" in x for x in results) else "good"
        rows.append(f"""
    <div class="step {verdict}">
      <div class="sn">Step {i}</div>
      <div class="body">
        {f'<div class="eval">👁 {esc(ev)}</div>' if ev else ''}
        {f'<div class="mem">🧠 {esc(mem)}</div>' if mem else ''}
        {f'<div class="goal">🎯 {esc(goal)}</div>' if goal else ''}
        {''.join(f'<div class="action">▶ {a}</div>' for a in acts)}
        {''.join(results)}
      </div>
    </div>""")

    goal_raw = d.get("goal", "") or ""
    # The task often embeds the whole skill prompt before "User request:".
    # Show only the real request prominently; tuck the skill text away.
    if "User request:" in goal_raw:
        skill_part, _, req_part = goal_raw.partition("User request:")
        goal_text = esc(req_part.strip())
        skill_html = (
            f"<details class='skill'><summary>Skill instructions (injected)</summary>"
            f"<pre>{esc(skill_part.strip())}</pre></details>"
        )
    else:
        goal_text = esc(goal_raw)
        skill_html = ""
    summary = esc(d.get("summary", "")).replace("\n", "<br>")
    ok = d.get("ok")
    badge = "SUCCESS ✅" if ok else "FAILED ⚠️"
    badge_cls = "bok" if ok else "bfail"

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BrowserUseBot — Run trajectory</title>
<style>
  :root {{ --bg:#0f1117; --card:#171a23; --mut:#8b93a7; --acc:#5b9dff; --ok:#2ecc71; --err:#ff6b6b; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:#e6e9ef; font:14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif; }}
  header {{ padding:24px 28px; border-bottom:1px solid #232838; position:sticky; top:0; background:var(--bg); z-index:2; }}
  h1 {{ margin:0 0 6px; font-size:20px; }}
  .goal {{ color:var(--mut); }}
  .badge {{ display:inline-block; padding:3px 10px; border-radius:999px; font-weight:600; font-size:12px; }}
  .bok {{ background:rgba(46,204,113,.15); color:var(--ok); }}
  .bfail {{ background:rgba(255,107,107,.15); color:var(--err); }}
  main {{ max-width:920px; margin:0 auto; padding:22px 18px 60px; }}
  .step {{ display:flex; gap:14px; background:var(--card); border:1px solid #232838; border-left:3px solid var(--acc); border-radius:10px; padding:14px 16px; margin:12px 0; }}
  .step.err {{ border-left-color:var(--err); }}
  .sn {{ min-width:64px; font-weight:700; color:var(--acc); }}
  .step.err .sn {{ color:var(--err); }}
  .body {{ flex:1; }}
  .body > div {{ margin:3px 0; }}
  .eval {{ color:#cdd3e0; }} .mem {{ color:var(--mut); font-size:13px; }}
  .goal {{ color:#e6e9ef; }}
  .action {{ background:#0d1a2e; border:1px solid #1e3a5f; border-radius:6px; padding:6px 9px; font-family:ui-monospace,Consolas,monospace; font-size:12.5px; overflow-x:auto; }}
  .act {{ color:var(--acc); font-weight:700; }} .pv {{ color:#9fe6b0; }}
  .err {{ background:rgba(255,107,107,.08); border:1px solid #3a2330; border-radius:6px; padding:6px 9px; color:#ffb3b3; font-family:ui-monospace,monospace; font-size:12.5px; }}
  .ok {{ background:rgba(46,204,113,.06); border:1px solid #1f3a2c; border-radius:6px; padding:6px 9px; font-family:ui-monospace,monospace; font-size:12.5px; white-space:pre-wrap; }}
  .result {{ background:var(--card); border:1px solid #232838; border-radius:10px; padding:16px 18px; margin-top:18px; }}
  .result h2 {{ margin:0 0 10px; font-size:16px; }}
  .stats {{ color:var(--mut); font-size:13px; margin-top:6px; }}
  details.skill {{ margin-top:8px; color:var(--mut); font-size:12.5px; }}
  details.skill summary {{ cursor:pointer; color:var(--acc); }}
  details.skill pre {{ white-space:pre-wrap; background:var(--card); border:1px solid #232838; border-radius:8px; padding:10px 12px; margin:8px 0 0; max-height:260px; overflow:auto; }}
</style></head>
<body>
<header>
  <h1>BrowserUseBot — CodeAct run trajectory <span class="badge {badge_cls}">{badge}</span></h1>
  <div class="goal">🎯 {goal_text}</div>
  {skill_html}
  <div class="stats">Steps: {d.get('steps')} · needed_human: {d.get('needed_human')} · model: Azure gpt-5.4 · tool: run_js (CodeAct)</div>
</header>
<main>
  {''.join(rows)}
  <div class="result">
    <h2>📦 Final Result</h2>
    <div>{summary}</div>
  </div>
</main>
</body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print("wrote", out, len(doc), "bytes")


if __name__ == "__main__":
    main()
