"""Three ORTHOGONAL lifecycle state machines (727 §3 / §11).

`quantos/contracts/research.py::AlphaState` is one MIXED field whose values
APPROVED / DEGRADED / PAUSED each encode two machines at once, so three of the
five composite states §3.4 asks for cannot be expressed — "research valid but
exchange disconnected", "research valid but regime unfit" and "data lineage
broken" all collapse into the same PAUSED, leaving an operator unable to answer
why it stopped, which is step one of §11.3's recovery flow.

This package splits that into:

    research_state.py    does the research hold up
    deployment_state.py  where does it run          <- the only human-gated machine
    health_state.py      is it behaving right now
    projection.py        legacy AlphaState -> the triple, with honest loss markers

`AlphaState` is RETAINED, not replaced. Its values are baked into
`AlphaTransition.evidence_hash` (a prev_hash chain) and into stored Deployment
Grant subjects, so renaming one would invalidate historical audit chains and
grants already signed by three real humans. Design + 4-commit migration plan:
`docs/engineering/specs/2026-07-27-three-orthogonal-state-machines-design.md`.

NOT to be confused with `services/alpha/lifecycle.py`: that is the ORCHESTRATOR
(audit chain, guards, catalog, persistence). This package is pure data + pure
functions with no I/O and no imports from `services/*`, so the orchestrator can
adopt it without creating an import cycle.

C1 scope: these machines exist and are tested, and nothing calls them yet —
adopting them is C3, the only commit in the plan that changes runtime behavior.
"""

from .deployment_state import (
    DE_RISKED_STATES,
    DEPLOYMENT_ALLOWED_TRANSITIONS,
    REAL_CAPITAL_STATES,
    DeploymentState,
    InvalidDeploymentTransition,
    UnauthorizedDeployment,
    authorize_deployment_transition,
    can_deployment_transition,
    is_de_risking,
    is_deployed,
    requires_human_authorization,
    risk_rank,
)
from .health_state import (
    HEALTH_ALLOWED_TRANSITIONS,
    HEALTH_FORCES_DE_RISK,
    HealthReason,
    HealthState,
    HealthVerdict,
    InvalidHealthTransition,
    MissingHealthReason,
    can_health_transition,
    health_agent_reachable,
)
from .projection import (
    LOSSY_DEPLOYMENT,
    LOSSY_HEALTH,
    LOSSY_HEALTH_REASON,
    LOSSY_RESEARCH_CHALLENGED,
    LifecycleTriple,
    check_composite_invariants,
    project,
)
from .research_state import (
    RESEARCH_ALLOWED_TRANSITIONS,
    RESEARCH_DEPLOYABLE,
    RESEARCH_TERMINAL_STATES,
    InvalidResearchTransition,
    ResearchState,
    can_research_transition,
    is_research_terminal,
    research_agent_reachable,
)

__all__ = [
    # research
    "ResearchState",
    "RESEARCH_ALLOWED_TRANSITIONS",
    "RESEARCH_TERMINAL_STATES",
    "RESEARCH_DEPLOYABLE",
    "InvalidResearchTransition",
    "can_research_transition",
    "is_research_terminal",
    "research_agent_reachable",
    # deployment
    "DeploymentState",
    "DEPLOYMENT_ALLOWED_TRANSITIONS",
    "REAL_CAPITAL_STATES",
    "DE_RISKED_STATES",
    "InvalidDeploymentTransition",
    "UnauthorizedDeployment",
    "can_deployment_transition",
    "requires_human_authorization",
    "authorize_deployment_transition",
    "is_deployed",
    "risk_rank",
    "is_de_risking",
    # health
    "HealthState",
    "HealthReason",
    "HealthVerdict",
    "HEALTH_ALLOWED_TRANSITIONS",
    "HEALTH_FORCES_DE_RISK",
    "InvalidHealthTransition",
    "MissingHealthReason",
    "can_health_transition",
    "health_agent_reachable",
    # projection
    "LifecycleTriple",
    "project",
    "check_composite_invariants",
    "LOSSY_DEPLOYMENT",
    "LOSSY_HEALTH",
    "LOSSY_HEALTH_REASON",
    "LOSSY_RESEARCH_CHALLENGED",
]
