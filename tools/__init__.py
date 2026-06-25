"""Out-of-gate utilities (NOT part of the deterministic research core or the gate).

Anything here may touch the network or read a local read-only data key. The governance
scanner and validate.py deliberately scope to quantos/+services/; tools/ is the quarantine
where the (read-only, no-SDK) network fetchers live so the research core stays offline.
"""
