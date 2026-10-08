"""QUANTOS canonical namespace.

Single, collision-free import root for cross-cutting contracts shared by every
service (avoids the two competing `services` package trees and never shadows the
stdlib `platform` module). See docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md (Phase 1).
"""
