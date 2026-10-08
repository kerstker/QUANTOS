"""QUANTOS Feature Layer (blueprint §02 / Phase 3).

Leakage-free feature engineering on top of the PIT data layer. Feature values
use only data at-or-before their event_time, and a feature value's
knowledge_time is the latest knowledge_time of its inputs (so it is knowable no
earlier than its inputs). Leakage is checked both statically and dynamically.
"""
