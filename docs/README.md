# QUANTOS documentation map

This page is the entry point for repository documentation. It distinguishes current instructions from historical design and audit records.

## Authority order

When documents disagree, use this order:

1. **Code, tests, and current machine evidence** — executable truth, especially `scripts/validate.py`, governance tests, and `evidence/*.json`.
2. **Safety policy** — [AGENTS.md](../AGENTS.md) and [SECURITY.md](../SECURITY.md).
3. **Current project state and delivery roadmap** — [PROJECT_STATE.md](PROJECT_STATE.md) and [QUANTOS_LEAN_INSTITUTIONAL_PRODUCTION_ROADMAP.md](QUANTOS_LEAN_INSTITUTIONAL_PRODUCTION_ROADMAP.md).
4. **Current architecture, workflow, data, and runbooks** — the curated links below.
5. **Dated specs, plans, audits, and reports** — historical evidence of intent or a point-in-time review; never assume they describe the current system.

`CLAUDE.md` is a compatibility pointer, not a second system inventory.

## Start here

| Need | Document |
|---|---|
| Understand the product | [README.md](../README.md) |
| Understand system boundaries | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Check current implementation history | [PROJECT_STATE.md](PROJECT_STATE.md) |
| Follow current delivery priorities | [Lean institutional roadmap](QUANTOS_LEAN_INSTITUTIONAL_PRODUCTION_ROADMAP.md) |
| Run a research workflow | [RESEARCH_WORKFLOW.md](RESEARCH_WORKFLOW.md) |
| Understand approved data surfaces | [DATA_SOURCES.md](DATA_SOURCES.md) |
| Start the local console | [Local research stack](runbooks/local-research-stack.md) |
| Understand deployment boundaries | [deploy/README.md](../deploy/README.md) |
| Operate L2 research services | [L2 research operations](runbooks/l2-research-operations.md) |
| Operate hourly alpha jobs | [Hourly alpha operations](runbooks/hourly-alpha-operations.md) |
| Back up and restore authority data | [Backup/restore runbook](runbooks/backup-restore-runbook.md) |
| Review safety rules | [SECURITY.md](../SECURITY.md) |
| Contribute code | [CONTRIBUTING.md](../CONTRIBUTING.md) |

## Documentation classes

```text
docs/
├── README.md                 Current navigation and authority rules
├── PROJECT_STATE.md          Living append-only implementation/handoff log
├── runbooks/                 Current operator procedures
├── research/                 Curated research findings
├── engineering/
│   ├── specs/                Dated design records
│   └── plans/                Dated implementation plans
├── audit/                    Point-in-time audits plus explicitly living risk registers
├── reports/                  Generated or dated operational reports
├── _archive/                 Superseded material retained only for traceability
└── investor/                 Presentation sources and approved media assets
```

Historical engineering records are intentional. They preserve the `spec → plan → tests → implementation` chain and should not be bulk-deleted merely because they are old. They should, however, be excluded from current navigation unless still actionable.

## Current living documents

- [PROJECT_STATE.md](PROJECT_STATE.md) — implementation chronology and handoff notes.
- [QUANTOS_LEAN_INSTITUTIONAL_PRODUCTION_ROADMAP.md](QUANTOS_LEAN_INSTITUTIONAL_PRODUCTION_ROADMAP.md) — current ordered roadmap.
- [QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md](QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md) — detailed capability remediation ledger.
- [QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md](QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md) — human-readable capability matrix; machine verdict remains in `evidence/top_tier_assessment.json`.
- [audit/RISK_REGISTER.md](audit/RISK_REGISTER.md) — living risk register; entries must be updated consistently.
- [PHASE_3A_GO_NO_GO_CHECKLIST.md](PHASE_3A_GO_NO_GO_CHECKLIST.md) — human decision checklist. Its existence does not grant readiness.

## Historical material

These categories are useful for traceability but are not operational instructions:

- all dated files under `docs/engineering/specs/` and `docs/engineering/plans/`;
- dated audits under `docs/audit/` unless explicitly marked living;
- `docs/phase2a/`, `docs/phase2f/`, and `docs/phase2h/`;
- older top-level gap analyses, audits, and roadmaps retained for decision history;
- `_archive/`, which is never authoritative.

The obsolete root-level completion reports and the June documentation control checklist were removed in August 2026. Git history remains the recovery mechanism; new redirect files should not be created for deleted status snapshots.

## Adding or updating documentation

- Update an existing living document instead of creating another competing “current state” file.
- Put new design decisions in a dated spec and implementation sequence in a dated plan.
- Put operational procedures in `runbooks/` and make every destructive step explicit and bounded.
- Put generated snapshots in `evidence/` or `reports/`; do not hand-edit canonical machine evidence.
- Keep secrets, raw data, runtime databases, and local paths out of documentation.
- Do not create distributed empty-directory markers. Track future infrastructure centrally in [PLANNED_SCAFFOLDS.md](PLANNED_SCAFFOLDS.md).
- Run `python scripts/check_repository_hygiene.py` after changing curated docs.

## Machine evidence

Human-readable documentation never upgrades readiness. Before making a claim, inspect the corresponding evidence:

- [validation report](../evidence/validation_report.json)
- [top-tier assessment](../evidence/top_tier_assessment.json)
- [paper-cycle snapshot](../evidence/alpha_paper_cycle.json)
- [autonomy readiness](../evidence/autonomy_readiness.json)
- [trial-ledger integrity](../evidence/trial_ledger_integrity.json)

Unknown, stale, malformed, empty, or failed evidence is not approval.
