import os
from browseruse_bot.platform import config  # loads .env
from langfuse import Langfuse

c = Langfuse(
    public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
    secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
    host=os.getenv("LANGFUSE_HOST"),
)

traces = [x for x in c.fetch_traces(limit=10).data if x.name == "task_run"]
t = max(traces, key=lambda x: getattr(x, "total_observations", 0) or 0)
obs = sorted(c.fetch_observations(trace_id=t.id, limit=100).data, key=lambda o: o.start_time)

tot_lat = tot_cost = 0.0
tin = tout = 0
for o in obs:
    lat = (o.end_time - o.start_time).total_seconds() if o.end_time else 0.0
    cost = o.calculated_total_cost or 0.0
    it = (o.usage.input if o.usage else 0) or 0
    ot = (o.usage.output if o.usage else 0) or 0
    tot_lat += lat
    tot_cost += cost
    tin += it
    tout += ot
    print("{:12} model={:10} lat={:6.1f}s  in={:6} out={:5}  cost={:.4f}".format(
        o.name, str(o.model), lat, it, ot, cost))

print("-" * 60)
print("TOTAL LLM: lat={:.1f}s  tokens in={} out={}  cost=${:.4f}".format(tot_lat, tin, tout, tot_cost))
print("wall (first->last obs): {:.1f}s".format((obs[-1].end_time - obs[0].start_time).total_seconds()))
