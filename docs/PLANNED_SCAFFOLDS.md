# Planned subsystems without committed scaffolding

Status: **CURRENT POLICY**  
Scope: repository structure only; this document grants no deployment or trading authority.

QUANTOS records future subsystems here instead of committing large empty directory trees or one `PLANNED.md` file per hypothetical component.

## Policy

- A directory enters Git when it contains a reviewed contract, implementation, configuration, schema, or test—not merely to advertise a future idea.
- Planned code paths must remain code-free until a real implementation is accepted.
- When a subsystem becomes real, remove it from this list in the same change that adds its tests and implementation.
- Do not infer readiness from a directory name.

## Planned paths

| Logical path | Intended capability | Current reality |
|---|---|---|
| `services/gateway/` | External API gateway and independently operated authentication boundary | Not implemented. Current read-only and guarded APIs remain separate services. |
| `services/learning/` | Online learning/retraining service | Not implemented. Current ML research is deterministic and offline. |
| `platform/` | Kubernetes, Terraform, GitOps, observability, secrets, and storage deployment definitions | Not deployed or evidenced. Phase 14 process isolation remains external work. |
| `schemas/avro/` | Avro event schemas | Not adopted. |
| `schemas/protobuf/` | Protobuf contracts | Not adopted. |
| `services/alpha/alpha_discovery_engine/` | DDD-style alpha discovery package | Not used; real search code lives in research/intelligence modules. |
| `services/alpha/alpha_validation/` | DDD-style alpha validation package | Not used; real validation code lives under `services/research/validation/`. |
| `services/data/data_quality_service/` | Standalone data-quality service | Not used; real quality logic lives in flat data modules. |
| `services/data/feature_store_service/` | Standalone feature-store service | Not used; current feature storage is local and PIT-aware. |
| `services/data/market_data_service/` | Standalone market-data service | Not used; public acquisition remains in `tools/`. |
| `services/data/tick_service/` | Dedicated tick service | Not implemented. |
| `services/research/model_registry/` | Standalone model registry | Not implemented. |
| `services/research/experiment_tracker/` | Alternate experiment tracker | Not used; the real registry is `services/research/experiment_registry/`. |
| `services/research/dataset_builder/` | Standalone dataset-builder package | Not implemented. |
| `services/research/research_agent_loop/` | Alternate agent package | Not used; guarded orchestration lives under `services/intelligence/`. |
| `services/portfolio/portfolio_research/` | Alternate portfolio research package | Not used; real code lives in `services/portfolio/`. |

## Real adjacent paths

- `config/` is real and contains versioned policy/coverage registries; it must not contain secrets.
- `schemas/events/`, `schemas/risk/`, and `schemas/trading/` contain real JSON schemas.
- `proto/README.md` is the single explicit notice that no `.proto` contract has been adopted.
- `deploy/console.Dockerfile` is a real read-only console image; it is not evidence of full platform deployment.

`tests/governance/test_planned_scaffold_boundary.py` enforces that planned code paths do not silently acquire dead Python modules. `scripts/check_repository_hygiene.py` prevents distributed placeholder markers from returning.
