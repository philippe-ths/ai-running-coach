"""Daily recovery signals (#555): a provider-agnostic store plus device adapters.

`port.py` is the contract an adapter meets, `store.py` the idempotent writes and
reads, `garmin_adapter.py` the first (unofficial, owner-only) source, and
`sync.py` the orchestration the daily job runs. Nothing outside `garmin_adapter.py`
knows Garmin exists, so the official API can replace it without touching the rest.
"""
