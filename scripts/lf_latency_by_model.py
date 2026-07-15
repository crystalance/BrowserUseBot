import os
import statistics as st
from browseruse_bot.platform import config  # loads .env
from langfuse import Langfuse

c = Langfuse(
    public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
    secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
    host=os.getenv("LANGFUSE_HOST"),
)

by_model: dict[str, list[float]] = {}
page = 1
while page <= 20:
    res = c.fetch_observations(name="agent_step", limit=100, page=page)
    data = res.data
    if not data:
        break
    for o in data:
        if not o.end_time or not o.start_time:
            continue
        lat = (o.end_time - o.start_time).total_seconds()
        by_model.setdefault(str(o.model), []).append(lat)
    page += 1

for model, lats in sorted(by_model.items()):
    if not lats:
        continue
    lats.sort()
    print(f"{model:12} n={len(lats):4}  mean={st.mean(lats):6.1f}s  "
          f"median={st.median(lats):6.1f}s  p90={lats[int(len(lats)*0.9)-1]:6.1f}s  "
          f"max={max(lats):6.1f}s")
