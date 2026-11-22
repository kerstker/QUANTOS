import json, sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.data.persistent_store import SqlitePITStore
from tools import ashare_kline_fetch as f

universe = json.load(open(f.UNIVERSE_PATH, encoding="utf-8"))["stocks"][:2]
store = SqlitePITStore(f.DB_PATH, create=True)
res = f.run_batch(universe, store, log_every=1)
print(json.dumps(res["stats"], indent=1))
print("failed:", res["failed"])
bar = store.latest_as_of(universe[0]["code"], 99999999)
print("latest_as_of", universe[0]["code"], "->", bar.event_time if bar else None,
      bar.close if bar else None)
