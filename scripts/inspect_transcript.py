import json
import sys

d = json.load(open(sys.argv[1], encoding="utf-8"))
steps = d["history"]["history"]
prev = None
for i, s in enumerate(steps):
    mo = s.get("model_output") or {}
    g = (mo.get("next_goal") or "").replace("\n", " ")
    acts = [list(a.keys())[0] for a in (mo.get("action") or []) if a]
    md = s.get("metadata") or {}
    t = md.get("step_start_time")
    dur = ""
    if prev is not None and t is not None:
        dur = f"{t - prev:.0f}s"
    prev = t
    print(f"{i+1:2} {dur:>5}  {','.join(acts):20}  {g[:72]}")
