# White Reality Check and Hansen SPA Family-Evidence Design

Date: 2026-07-18
Status: approved after independent safety/statistics review; ready for implementation planning
Safety level: `L2_DRY_RUN_ONLY`

## 1. Purpose

QUANTOS already records the global trial population used by Deflated Sharpe,
applies BH-FDR to ranked candidates, computes PBO/CPCV diagnostics, and preserves
search-to-evaluation provenance. Those controls do not answer one narrower
question:

> After searching an entire candidate family, is the best observed net OOS
> return differential still significant when the family-wise data-snooping
> process and serial dependence are represented directly?

This design adds White's Reality Check (RC) and Hansen's Superior Predictive
Ability (SPA) test as a deterministic validation primitive and binds the result
to an immutable, complete-family evidence record.

The work is deliberately split into two milestones:

1. **Phase A - deterministic diagnostic evidence.** Implement and validate the
   statistics kernel, governed-family contracts, append-only persistence,
   blind common-OOS evaluation receipts, and a content-addressed evidence
   export. It has no lifecycle or promotion effect.
2. **Phase B - deferred-promotion hard gate.** Refactor the official persistent
   search path so no candidate is promoted before its family closes, and only
   the manifest-selected winner can consume a verified family assessment.

Phase A must be independently reviewed and accepted before Phase B changes any
alpha lifecycle behavior. Merely adding the kernel or schema does not close a
top-tier readiness gap; only a valid assessment over real, complete candidate
returns can supply statistical evidence.

## 2. Current gap and honest baseline

The current experiment authority has candidate registrations and immutable
aggregate evaluation JSON, but it does not preserve a common-period return
matrix for every performance-eligible candidate. The mutable `alpha_state`
return field is not a complete-family authority and cannot be used to recreate
historical selection evidence.

The current mass-search material contains 39 registered candidates, only eight
linked evaluations, and no immutable disposition or period-return record for
the other 31. Separate searches also use different dataset versions. Therefore
the current material belongs only to a **historical audit scope** whose result
is mechanically `NOT_ASSESSABLE`; it must not be migrated into a declared
family, padded, pooled across incompatible datasets, represented as a Reality
Check, or used to create a `DataSnoopingEvidenceRef`.

The natural-forward baseline remains 0/8 admitted alphas. RC/SPA is a
data-snooping control over a searched historical family; it is not natural
forward evidence and cannot substitute for that baseline.

## 3. Statistical contract

### 3.1 Inputs and sign convention

For one frozen, blindly evaluated family:

- `T` is the number of common evaluation periods;
- `K` is the number of unique performance-eligible hypothesis configurations;
- `r[t, k]` is candidate `k`'s no-peek, net-of-declared-cost return in period
  `t`;
- `r0[t]` is the preregistered benchmark's net return on the same period; and
- `d[t, k] = r[t, k] - r0[t]` is the performance differential, so positive is
  better.

Equivalently, using losses, `d` is benchmark loss minus candidate loss. The
first supported objective is exactly `MEAN_NET_RETURN_DIFFERENTIAL`. Sharpe,
drawdown, IC, gross return, and evaluator verdicts cannot be substituted into
this matrix.

All candidates are generated without access to this family OOS window. After
all linked searches finish and their emission seals are frozen, a single
`FamilyOosEvaluationService` releases the preregistered held-out window and
evaluates every unique eligible configuration on it. Search/inner-validation
scores may guide generation before freeze, but they are not matrix columns and
cannot be substituted for the blind family OOS results.

All candidates use the same non-empty ordered period keys, dataset version,
point-in-time universe, label horizon, purge/embargo policy, cost model,
benchmark, and return currency/scale. A period key contains a positive Unix
epoch-microsecond event time, an explicit UTC frequency ID, and the declared
label horizon. Keys must be unique and strictly increasing; publishable
evidence forbids `PanelPeriod.event_time=None`. Inputs are finite IEEE-754
float64 values. Ragged matrices, NaN/Inf, duplicate/non-monotone keys,
misaligned calendars, mixed timezone/frequency semantics, or candidate-specific
missing periods make the family `NOT_ASSESSABLE`; there is no
intersection-after-the-fact or imputation.

Minimum validity is:

- `K >= 2`;
- a non-empty preregistered block-length grid in which every
  `1 <= block_length <= floor(T / 2)`;
- `T >= max(20, 2 * max(block_lengths))`;
- an explicit `alpha` in `(0, 1)`;
- an explicit unsigned 64-bit `seed`; and
- at least 10,000 bootstrap replications for publishable evidence.

Smaller replication counts are allowed only in unit tests and carry the status
`NON_PUBLISHABLE_TEST_RUN`.

The pure statistical kernel accepts the complete unsigned-64 seed domain. The
exact-v3 governed declaration and family-search contracts deliberately admit
the SQLite-safe subset `0 <= seed <= 2^63 - 1`, because both persisted seed
columns use lossless SQLite `INTEGER` values. Values outside that subset are
rejected at the contract boundary; they are never truncated, wrapped, or
silently converted. This still supplies 63 bits of deterministic seed entropy
while keeping the domain contract, canonical row hash, SQLite value, and UDF
input byte-for-byte consistent.

### 3.2 White Reality Check

Let `mean_k` be the sample mean of column `k`. The observed unstudentized RC
statistic is:

```text
RC_observed = max_k(sqrt(T) * mean_k)
```

For stationary-bootstrap replication `b`, let `mean_star[b, k]` be the mean
formed from the resampled time indices. White's null distribution centers every
candidate at its observed mean:

```text
RC_star[b] = max_k(sqrt(T) * (mean_star[b, k] - mean_k))
```

The same resampled time-index vector is used for every candidate in a
replication, preserving cross-candidate dependence.

### 3.3 Hansen SPA

SPA uses a studentized maximum to reduce sensitivity to noisy and irrelevant
alternatives. For each differential column, compute the stationary-bootstrap
long-run variance estimator:

```text
x[t, k] = d[t, k] - mean_k
p = 1 / block_length
gamma[i, k] = (1 / T) * sum_{t=1}^{T-i} x[t, k] * x[t+i, k]
kappa[i] = (1 - i/T) * (1 - p)^i + (i/T) * (1 - p)^(T-i)
omega2[k] = gamma[0, k] + 2 * sum_{i=1}^{T-1} kappa[i] * gamma[i, k]
```

The observed statistic is:

```text
SPA_observed = max(0, max_k(sqrt(T) * mean_k / sqrt(omega2[k])))
```

A non-finite, non-positive, or numerically degenerate `omega2` for any
performance-eligible candidate makes the entire combined assessment
`NOT_ASSESSABLE`. The exact degeneracy floor is
`64 * float64_epsilon * gamma[0, k]`; `gamma[0, k] == 0` is degenerate by
definition. The implementation records both `gamma[0, k]` and the floor. It
does not add an epsilon, drop the column, or silently switch SPA to an
unstudentized statistic.

The kernel computes all three Hansen recentering variants. Define:

```text
threshold[k] = -sqrt((omega2[k] / T) * 2 * log(log(T)))
center_lower[k] = max(mean_k, 0)
center_consistent[k] = mean_k if mean_k >= threshold[k] else 0
center_upper[k] = mean_k
```

For variant `v`:

```text
SPA_star_v[b] = max(0, max_k(sqrt(T) *
                    (mean_star[b, k] - center_v[k]) /
                    sqrt(omega2[k])))
```

`consistent` is the primary SPA result. `lower` and `upper` are retained as
diagnostics and bounds; an operator cannot choose a variant after seeing the
result.

### 3.4 P-values and Monte Carlo uncertainty

Every p-value uses the finite-simulation plus-one rule and an inclusive tail.
If `C` is the number of exceedances among `B` replications and `q` is the
unknown conditional exceedance probability, then:

```text
C ~ Binomial(B, q)
p_plus = (1 + C) / (B + 1)
Var(p_plus | q) = B * q * (1 - q) / (B + 1)^2
                 <= B / (4 * (B + 1)^2)
mc_radius_2se(B) = 2 * sqrt(B / (4 * (B + 1)^2))
                  = sqrt(B) / (B + 1)
```

This prevents a reported p-value of zero. Because `q` is unknown, a plug-in
standard error is diagnostic only. The load-bearing ambiguity rule uses the
distribution-free conservative two-standard-error radius above. The result
stores `C`, `B`, `p_plus`, the exact variance formula, the conservative radius,
and whether `abs(p_plus - alpha) <= mc_radius_2se(B)`. There is no claim that
`sqrt(p_plus * (1 - p_plus) / (B + 1))` is an exact Monte Carlo standard error.

The declaration contains an ordered replication schedule, for example
`10000, 50000, 200000`. If a result is inside the conservative uncertainty
radius and a declared larger count remains, its status is
`PENDING_MORE_REPLICATIONS` and it is
non-promotable. The runner automatically advances to the next count. A larger
run must reuse the exact first `B_previous` bootstrap replications and append
only the next deterministic stream segment; every prefix stream hash is stored.
If the last declared count is still inside the radius, the final status is
`MONTE_CARLO_UNRESOLVED` and cannot promote. An operator cannot reset the seed,
discard a prefix, or add replications selectively after viewing the outcome.

### 3.5 Deterministic stationary bootstrap

The implementation does not depend on NumPy's evolving PRNG sequence. It uses
the versioned local algorithm `QUANTOS_STATIONARY_BOOTSTRAP_V1`. For each
preregistered block length, the unsigned 64-bit declared seed is the initial
SplitMix64 state. Every addition, multiplication, and xor result is masked by
`0xffffffffffffffff`; `next_u64()` is exactly:

```text
state = (state + 0x9e3779b97f4a7c15) mod 2^64
z = state
z = ((z xor (z >> 30)) * 0xbf58476d1ce4e5b9) mod 2^64
z = ((z xor (z >> 27)) * 0x94d049bb133111eb) mod 2^64
return z xor (z >> 31)
```

`uniform_below(n)` requires `1 <= n <= 2^64`, computes
`limit = 2^64 - (2^64 mod n)`, discards draws `x >= limit`, and returns
`x mod n`. A restart uniform is
`u = (next_u64() >> 11) * 2^-53`, exactly in `[0, 1)`; restart occurs only when
`u < 1 / block_length` (strict less-than).

For each replication in ascending sequence order:

1. Draw the first index with `uniform_below(T)`.
2. For each of the remaining `T - 1` positions, draw one restart uniform.
   On restart draw a new `uniform_below(T)` index; otherwise advance the
   previous index by one modulo `T`.
3. Reuse the complete index vector for every candidate column, which is sorted
   by config hash before statistics are calculated.

Index-stream bytes are the concatenation of every emitted index, replication
major and period minor, encoded as an unsigned 64-bit little-endian integer.
The declaration separately binds `T`, replication count, and block length.
Every scheduled prefix stores the SHA-256 of exactly those bytes; rejection
draws affect the deterministic PRNG state but are not themselves encoded. Each
block length starts from the same declared seed and has a separately named
stream/hash, so an operator cannot switch or splice streams after observing a
result.

NumPy float64 arrays are allowed for indexed reductions and performance; NumPy
is already a pinned non-network dependency. Evidence reproducibility is defined
inside the repository's pinned runtime. Calculations retain float64 precision.
Every zero is canonicalized to positive zero before calculation and storage;
all other load-bearing floats in authority JSON and published evidence are
encoded as finite Python `float.hex()` strings. P-values additionally retain
the integer exceedance count and denominator. Human-readable decimal summaries
are marked non-authoritative.

`QUANTOS_CANONICAL_JSON_V1` recursively requires Unicode strings in NFC form,
rejects duplicate object keys on load, uses `ensure_ascii=False`,
`sort_keys=True`, compact separators `(',', ':')`, UTF-8 without BOM, and
exactly one final LF. Non-NFC strings, raw JSON numeric floats, NaN/Inf, and
duplicate keys are rejected at the authority boundary. A negative zero supplied
through a typed contract is normalized to positive zero before any statistic or
hash, so it never reaches canonical JSON under a second identity. SHA-256 always
covers these exact bytes. The
committed evaluation-code manifest identifies a clean full commit and hashes
the exact Git blob bytes for every declared path with SHA-256; it never hashes
checkout-translated worktree bytes. No BLAS-dependent model fitting or parallel
reduction is part of the kernel.

### 3.6 Block-length policy and assumptions

Publishable evidence uses `QUANTOS_BLOCK_LENGTH_GRID_V1`, fixed before the OOS
window is released. With known common-calendar length `T`, let `L0` be the
largest positive integer satisfying `L0^3 <= T` (integer arithmetic only). The
ordered grid is the sorted unique set:

```text
max(1, floor(L0 / 2)), L0, min(floor(T / 2), 2 * L0)
```

Every grid point is computed with the complete replication schedule. For each
primary method, the load-bearing p-value is the maximum p-value over the grid.
Both methods must reject at every grid point and be outside every conservative
Monte Carlo uncertainty radius;
an invalid variance, unresolved schedule, or method disagreement at any grid
point is non-promotable. Custom or single post-result block lengths are
diagnostic only and cannot create a publishable EvidenceRef.

The declaration and artifact state the stationary/weak-dependence and mixing
assumptions required by the stationary bootstrap. Passing numerical tests does
not prove those assumptions; a declared diagnostic records their review status,
and an unreviewed or contradicted assumption makes promotion evidence
non-publishable.

## 4. Complete-family definition

### 4.1 Family membership

A family is every raw proposal and unique hypothesis configuration whose
performance could influence one governed selection/deployment decision. The
authority, not a caller, first issues an immutable `selection_scope_id` for one
deployment mandate: target portfolio sleeve/capital/risk budget,
market/universe domain, label/evaluation horizon, benchmark, and cost-policy
class. This scope deliberately spans research questions and strategy lineage
roots whenever their outputs could compete for that same mandate.

Within the scope, the authority issues immutable `research_program_id` values
for continuing research lines and one monotone `selection_decision_id`; it
derives `family_id` from that decision. Question IDs and globally registered
lineage roots remain provenance dimensions, not keys that allocate another
publishable slot. Requester, owner, free-text title, process ID, research
question, and strategy class cannot reset the selection scope. The authority
maintains unique deployment-mandate aliases; creating a genuinely distinct
scope is a separately audited multi-party governance action, not a search API
parameter. A caller cannot mint, reuse, relink, or choose any of these IDs.

Version 1 permits exactly one publishable decision/family per selection scope.
Before any search starts, an append-only CAS reserves that scope's sole
publishable slot for one decision. Every later decision/program in the scope is
authority-marked `DIAGNOSTIC_ONLY` and can never create a promotable EvidenceRef,
regardless of p-values, question, lineage root, or fresh OOS exposure. If
several questions, strategy classes, or search branches may compete for the
mandate, all must be linked into the one governed family before freeze, whose
declaration freezes the complete participating question and lineage-root sets.

Historical validity and new promotion eligibility are different states. Before
any diagnostic sibling can receive OOS bytes, the selection scope must have
exactly one immutable `SelectionScopeOutcome`: either `WINNER_PINNED`, created
atomically with the publishable family's first alpha evidence pin and binding
that exact alpha/family/ref, or `CLOSED_WITHOUT_PROMOTION`, which permanently
prevents any later pin from that scope. Until one outcome exists, diagnostic OOS
release is forbidden. Thus later diagnostics cannot make an operator postpone
consuming an old ref, inspect another strategy class, and then choose the old
winner; the choice or no-promotion decision is already irreversible.

A family may contain multiple research programs and search attempts only when
the authority linked them to the same `selection_decision_id` before starting
and they share the search-data and common-OOS contracts. The verifier loads
every program/decision and OOS exposure in the selection scope so a later
strategy root cannot pretend a previously viewed holdout is fresh. Manual,
scratch, failed, or non-official
research that can influence the same decision must be preregistered through the
external-proposal ingestion sink before freeze; otherwise the selection
decision cannot claim complete-family evidence.

Proposal identity, hypothesis identity, and promotable alpha identity are
separate:

- `proposal_id` hashes `family_id`, `search_id`, the monotone emission ordinal,
  and `raw_envelope_hash` for a non-quarantined envelope. For a quarantined
  envelope it instead hashes only the family/search/ordinal plus the fixed
  `SECRET_QUARANTINED` marker; no public identity or chain head is derived from
  the secret bytes;
- `raw_envelope_hash` hashes the exact opaque envelope octets received at the
  registration boundary, before UTF-8 decoding, JSON parsing, normalization,
  or schema validation;
- `canonical_proposal_hash`, present only after successful decoding and
  structural parsing, hashes the separately stored canonical proposal bytes;
- `config_hash`, present only after structural validation, identifies one
  canonical hypothesis configuration; and
- `alpha_id` is the deterministic catalog identity assigned to that
  configuration and cannot be chosen after performance is observed.

Every generator must send a bounded opaque byte envelope to one append-only
registration sink before decoding or parsing it into `AlphaDefinition`, asking
any data provider for a score, or invoking an evaluator. Before parsing, the
sink computes the byte length and `raw_envelope_hash` in memory and runs the
authority-configured fail-closed secret/DLP scanner fixed by the declaration.
If the scanner passes, the exact octets are stored only in a restricted,
non-Git raw-envelope blob authority and the append-only proposal row binds its
content address, byte length, media-type declaration, scanner policy/version,
canonical result hash, and authority event/head. This is trusted-code unkeyed
L2 integrity, not an external signature. Public evidence never embeds those raw
octets. Only
after that row commits may parsing start; a successful parse appends the safe,
schema-constrained canonical proposal bytes and `canonical_proposal_hash` as a
separate record.

If the scanner reports a bearer token, API key, PEM/private-key material,
credential-shaped field, restricted-data signature, or high-entropy policy hit,
or if the scanner is unavailable/errors, the sink keeps the raw hash and any
result-derived commitment only inside the restricted quarantine authority. The
ordinary proposal authority persists only the safe public proposal ID, an
authority-issued opaque quarantine-record ID unrelated to the raw bytes, a
coarse declared length bucket, media type, scanner policy/version, and fixed
non-secret rule/reason codes. It does not persist the raw octets to the experiment database, ordinary
blob store, evidence tree, logs, or backup set; it appends a
`SECRET_QUARANTINED` terminal proposal status and permanently makes the family
`NOT_ASSESSABLE`. No parser, search, data, or evaluator receives the bytes.
Operational quarantine/incident handling is outside the statistical evidence
path. Redaction is forbidden because reversible or lossy rewriting cannot both
preserve exact raw-byte identity and prove that no secret was published.
Because L2 has no keyed/HMAC publication authority, no raw hash, scanner-result
hash derived from matched bytes, restricted chain head, or other value that can
validate an offline credential guess is exported. A live restricted verifier
may confirm the quarantine internally; an offline `NOT_ASSESSABLE` artifact
honestly proves only the safe public quarantine row and resulting family block.

Invalid UTF-8, malformed JSON, duplicate-key, non-NFC, syntax, and type failures
that passed the secret scan therefore retain an exact restricted-blob identity
without falsely claiming canonical proposal bytes. Every non-quarantined
proposal ends in exactly one proposal status:

- `PRE_EVALUATION_INVALID`, with a machine-verifiable structural or guardrail
  reason recorded before any performance value is calculated; or
- `CONFIG_ACCEPTED`, mapped to exactly one config hash and alpha identity.

Accepted duplicate emissions remain separate proposal rows and count toward the
declared raw-emission budget, but map to one unique hypothesis column. They may
not multiply or remove matrix columns and do not inflate the existing
DISTINCT-config Deflated-Sharpe trial count. The whole-family batch evaluates
each config hash exactly once; every duplicate proposal references that one
receipt, and a second receipt for the same config is rejected.

Four counters are stored and verified independently; none is inferred from a
different counter:

- `raw_proposal_count` is every registered opaque envelope, including invalid
  and byte-identical duplicate emissions;
- `unique_performance_hypothesis_count` is the number of distinct accepted
  `config_hash` values and therefore exactly the RC/SPA matrix column count;
- `global_unique_config_count` is the content-addressed global trial-ledger
  DISTINCT-config count at the bound history head and remains the existing DSR
  multiplicity input; the same config hash increments it at most once across
  all families; and
- `performance_access_attempt_count` is the number of committed whole-family
  OOS access attempts. It must be exactly one for an `ASSESSED` family.

Invalid envelopes and duplicate emissions affect `raw_proposal_count` and the
declared search budget, but cannot inflate `global_unique_config_count` without
a new distinct config hash. Family closure verifies all four values against
their respective append-only rows and the immutable global ledger proof.

After the family is frozen and blind evaluation completes or aborts, every
proposal has exactly one immutable `CandidateDisposition`:

- `PERFORMANCE_EVALUATED`, valid only for `CONFIG_ACCEPTED` and referencing the
  complete atomic common-OOS receipt for its unique config hash;
- `PERFORMANCE_ATTEMPTED_INCOMPLETE`, recording that OOS access/evaluation
  began but raised, timed out, or failed to commit a complete receipt; this
  mechanically makes closure `NOT_ASSESSABLE`; or
- `PRE_EVALUATION_INVALID`, valid only for a proposal rejected structurally
  before it formed an eligible hypothesis or accessed OOS. It never contributes
  a return column; or
- `SECRET_QUARANTINED`, valid only when the declaration-bound DLP scan denied or
  failed before parsing. Its public disposition binds only the opaque
  quarantine-record ID, length bucket, media type, policy version, and fixed
  reason code; restricted raw/result commitments are never exported. It
  contributes only to `raw_proposal_count`, never forms a canonical
  proposal/config/alpha or return column, and forces the whole family
  `NOT_ASSESSABLE`.

All accepted duplicate proposals mapping to the same config hash must reference
the same byte-identical successful receipt. If any accepted proposal instead
records `PERFORMANCE_ATTEMPTED_INCOMPLETE`, the family is not assessable even if
another duplicate completed.

Leakage failure after performance access, poor performance, an evaluation
exception, a timeout after evaluation begins, or a missing return series may
not be relabeled `PRE_EVALUATION_INVALID`. They must be
`PERFORMANCE_EVALUATED` with their actual results or
`PERFORMANCE_ATTEMPTED_INCOMPLETE`; the latter parks the entire family.

On search termination, the generator writes an immutable emission seal
containing its declared budget, actual raw proposal count, ordered proposal-ID,
non-quarantined raw-envelope-hash and optional canonical-proposal-hash digests,
ordered safe quarantine-record IDs, accepted-config digest, candidate-chain
head, and terminal reason. The family cannot freeze
unless every linked search is terminal and every seal matches the registered
rows. A proposal or evaluation observed without prior registration makes the
family `NOT_ASSESSABLE`; it is never repaired by late registration.

Only after all emission seals are verified does the authority write a family
freeze event. That event revokes all generator handles. Before any OOS byte is
made visible, `FamilyOosEvaluationService` must commit one whole-family
`OosAccessAttempt` binding the frozen config-set digest, benchmark, period-key
contract, `selection_decision_id`, and global `oos_exposure_id`. The same CAS
transaction permanently consumes the exposure handle. Only after that commit
returns may the data authority release bytes to this service principal. The
service evaluates the fixed, lexicographically ordered unique config set as one
batch without returning candidate results to any generator.

The handle is never retryable. A normal exception appends an immutable
`INCOMPLETE` terminal record. A process kill leaves the already durable
`STARTED` attempt, which is itself closure-blocking; deterministic startup
recovery appends `INCOMPLETE` for every orphan before any new work is accepted.
Neither path can allocate a replacement handle or make a second attempt for the
family. A successful batch atomically commits all candidate and benchmark
receipts and one `COMPLETED` terminal record. Search data and OOS data are
disjoint under the declared purge/embargo boundary. A search process that
accessed the OOS handle before freeze invalidates the family.

No proposal or hypothesis may be added after family freeze or closure. The declaration therefore
includes a mechanical search budget and stopping rule: allowed search kinds,
maximum/required search attempts and candidate counts, generator seeds, and
the only deterministic early-abort reasons. An operator cannot keep an open
family alive or close it based on observed performance.

A new related search after closure receives a new authority-issued sibling
decision and family under the same selection scope, but Version 1 marks it
`DIAGNOSTIC_ONLY`. A fresh global OOS exposure may support honest diagnostics;
it does not allocate a second publishable slot and cannot be released before the
selection-scope outcome above is committed. The verifier loads the scope's
sibling-decision and exposure history; a second publishable-slot reservation,
reused exposure ID, identical exposure fingerprint, overlapping held-out
period/universe/label contract, or any cross-family selection fails closed.
Combining old and new results post hoc is `NOT_ASSESSABLE`; an old family cannot
be reopened, relinked, or retroactively enlarged.

Version 1 deliberately provides no outer sequential-multiplicity or
super-family escape hatch. If a later decision is informed by any sibling OOS
result, its inner-family assessment cannot create a promotable EvidenceRef even
when it uses a fresh exposure. Supporting that workflow requires a separately
preregistered outer procedure that reserves all disjoint exposures before the
first release and controls selection across decisions; that procedure is
future work, not an operator waiver.

### 4.2 Preregistration

Before any linked search begins, the authority records a frozen
`DataSnoopingDeclaration` containing:

- authority-issued `selection_scope_id`, canonical deployment-mandate scope
  hash, linked `research_program_id` values, `selection_decision_id`, derived
  family ID, immutable predecessor/sibling history head, frozen participating
  research-question/lineage-root sets, and schema/algorithm versions;
- objective and deterministic winner-selection rule;
- distinct search-data handle and globally authority-issued
  `oos_exposure_id`, dataset versions, content hashes, non-overlap proof,
  release policy, one-shot access policy, and purge/embargo boundary;
- OOS universe ID/hash and strict ordered-period-key/calendar hash;
- feature/label horizon, purge, and embargo settings;
- return currency, scale, and compounding convention;
- cost model ID/hash and all cost parameters;
- benchmark ID, definition, and content hash;
- alpha, bootstrap/PRNG/canonical-JSON versions, deterministic block-length
  policy and exact grid, seed, and replication schedule;
- source commit, dirty-tree fingerprint, and a versioned evaluation-code
  manifest-policy ID, exact path-set hash, and manifest hash covering every
  strategy, return, cost, bootstrap, and family verification input that can
  affect the result;
- secret/DLP policy ID, scanner implementation/version, restricted raw-envelope
  retention authority, fail-closed quarantine policy, and public-artifact scan
  policy;
- linked search kinds, generator seeds, proposal/search budgets, deterministic
  stopping rule, external-proposal ingestion deadline, and the rule for
  attaching search IDs; and
- trial-ledger authority ID plus the declared meanings of
  `raw_proposal_count`, `unique_performance_hypothesis_count`,
  `global_unique_config_count`, and `performance_access_attempt_count`.

The declaration is valid only if it precedes every linked search/proposal and
the OOS authority proves that no generator principal received the common-OOS
handle before the freeze event. The global exposure registry is append-only,
uses a unique constraint on the canonical exposure fingerprint, and implements
the single `AVAILABLE -> CONSUMED` compare-and-swap as a guarded append-only
consumption insert unique on `oos_exposure_id` and bound to one
`selection_decision_id`. The fingerprint binds dataset bytes/version, universe,
ordered period keys, label horizon, and purge/embargo contract; issuing a new ID
cannot disguise the same exposure. Every verifier reloads the global exposure
and consumption rows plus all sibling exposure rows instead of trusting copies
embedded by the family. The declaration binds the selection-scope history chain head immediately before
its selection decision; the decision row links to that predecessor head, and
closure binds the head through the current decision. Verification walks every
earlier decision/exposure in monotone order and proves the bound head is an
unchanged prefix of the current append-only selection-scope history. Later
diagnostic siblings do not stale the artifact as a historical record, but the
selection-scope outcome rules decide whether its ref was already locked or can
no longer be consumed. A sibling verifier cannot reuse or overlap an earlier
exposure. Phase A can assess only an explicitly declared, blindly
evaluated family; historical undeclared searches remain a separate
`NOT_ASSESSABLE` audit scope.

A diagnostic declaration may record a dirty-tree fingerprint, but publishable
evidence and every Phase B promotion reference require a clean worktree bound
to a full commit hash. The code manifest is an ordered list of repository paths,
Git blob object identities, and SHA-256 digests of the exact committed blob
bytes; worktree newline translation is never an input. This prevents an
unrecorded local patch from becoming load-bearing evidence.

The path set is authority-derived under `CODE_MANIFEST_POLICY_V1`, never a
caller-supplied allowlist. The versioned policy fixes the official search,
strategy, detailed-evaluation, return/cost, bootstrap, canonical-codec, family
authority/verifier, lifecycle-guard, promotion, event-schema, and top-tier
entrypoint roots. The tools/deployment boundary computes the complete repo-local
transitive import closure from those roots and adds every module/file selected
through dynamic registries, plugin/DSL/operator catalogs, evaluator/cost/data
policies, event-schema catalogs, and versioned JSON configuration. It also binds
the Python/runtime dependency lockfiles and the policy file itself. Stdlib and
third-party source trees are not copied, but their pinned runtime/lock identities
are included.

Declaration and runtime verification independently regenerate the exact
canonical path set and path-set hash from the bound commit and policy version,
then require equality with the manifest before comparing blob digests. Missing
or extra paths, unresolved/dynamic imports, unknown registry entries, a changed
policy, or an omitted helper are `CODE_MANIFEST_INCOMPLETE` and fail closed.
The service verifier consumes the generated evidence without invoking Git; it
cannot accept a caller-provided path list.

Referencing a manifest is not proof that its code computed the return matrix.
Immediately before `begin_evaluation_batch` commits `STARTED` and before the
data authority releases any OOS byte, the deployment boundary must issue an
`ExecutionCodeAttestation` for the immutable runtime artifact and grant an
exclusive read lease that remains held through the terminal batch commit. The
attestation binds the declaration's exact commit, policy/path-set/blob manifest,
runtime and dependency-lock identities, immutable artifact ID/digest,
entrypoint, process-image identity, lease ID, and authority head. Every field
must equal the declaration-derived `CodeManifestEvidence`; a mutable worktree,
missing provider, or caller-constructed attestation fails before byte release.

The `STARTED` attempt stores the attestation and lease hashes. Every candidate,
benchmark, and batch receipt binds that same execution-attestation hash.
`commit_evaluation_batch` asks the provider for a terminal attestation under the
same lease and requires byte-identical manifest/runtime/artifact identity and an
unbroken lease. A changed helper later restored, a runtime-lock change, lease
loss, process-image change, or start/end mismatch permanently finalizes the
attempt as `INCOMPLETE` and makes the family `NOT_ASSESSABLE`; computed returns
are not committed and the OOS exposure is never retried. This machine path
prevents ordinary code-reference laundering while retaining the project's
explicit threat boundary: trusted same-process code could forge a provider, so
code review, governance tests, and future process isolation remain required.

A descendant commit has two deliberately separate meanings. It may publish and
verify an old artifact as historical evidence when the bound source commit and
embedded exact blobs remain available and hash correctly. It may consume that
artifact for a new lifecycle transition only when every current load-bearing
path has the identical Git blob identity/digest declared by the evidence and
the current versioned verifier policy explicitly accepts the artifact version.
A descendant that changes any strategy, return, cost, bootstrap, family,
evidence-verifier, lifecycle-guard, or promotion path in the manifest produces
`CODE_DRIFT`: the artifact remains an immutable historical record but is stale
and non-promotable. Changes outside the declared load-bearing manifest do not
require repository HEAD equality. A later commit can never edit the old
artifact or reinterpret a changed path as equivalent without a new declared
family evaluation.

### 4.3 Closure manifest

Closing the family constructs a canonical `DataSnoopingFamilyManifest` that
binds:

- the authority-issued `selection_scope_id`, canonical deployment-mandate and
  alias-authority heads, sole-publishable-slot reservation, exact linked
  program/question/lineage-root sets, `selection_decision_id`, derived family
  ID, declaration hash, scope outcome when present, and exact
  selection-scope decision/exposure-history head;
- the freeze event/hash, global `oos_exposure_id`/fingerprint, committed
  one-shot access-attempt record, and OOS-release receipt proving release after
  freeze and after durable handle consumption, plus the exact start/terminal
  `ExecutionCodeAttestation` and immutable-artifact lease proof;
- every linked search ID, terminal event hash, emission-seal hash, emitted
  count, and candidate-chain head;
- every proposal ID; for non-quarantined rows, the raw-envelope hash, optional
  canonical-proposal/config hash, exact length/media type, DLP policy/result
  hash, and restricted-clean-blob receipt; for quarantined rows, only the safe
  opaque quarantine-record ID, length bucket, media type, policy/reason code,
  deterministic no-secret-derived public identity, and disposition. The
  manifest never includes a quarantined raw/result hash or restricted chain
  head;
- every unique config hash and exact atomic evaluation-receipt hash, including
  evaluation `run_id`, search link, gross/cost/net/turnover series, and audit
  intent hash;
- the whole-family evaluation-batch hash and the independently verified
  `raw_proposal_count`, `unique_performance_hypothesis_count`,
  `global_unique_config_count`, and `performance_access_attempt_count`;
- ordered period keys and their hash;
- candidate and benchmark decomposed return-series hashes plus the cost-model
  input hash;
- the selected winner's config hash;
- content-addressed trial-ledger snapshot/history-proof paths and SHA-256
  digests, plus the history-chain head; and
- the declaration and actual-execution code-manifest hashes, immutable runtime
  artifact/lease identity, plus dataset/universe/cost/benchmark fingerprints.

Candidate columns are sorted by config hash. The selected winner is the
candidate with the highest sample mean net return differential, breaking an
exact numeric tie by lexicographically smallest config hash. That selection
rule is fixed in the declaration.

Any mismatch between declaration, closure manifest, database rows, trial
ledger, or current evidence reference is tampering or staleness, not an empty
family. The bound ledger history head may become an ancestor of a newer valid
global history head; unrelated later trials do not invalidate the family.
The same rule does not permit holdout reuse: the verifier resolves the bound
predecessor selection-scope history head through the selected decision, scans every
earlier sibling/exposure, and proves that its exposure was globally unique and
one-shot. A later valid sibling merely extends the chain; the global unique/CAS
constraints and that sibling's predecessor scan prevent it from reusing this
family's exposure.
Publishable evidence cannot rely only on the mutable fixed
`alpha_trial_ledger_snapshot.db` and history filenames because a later seal
replaces them. Closure copies their exact verified bytes to immutable
content-addressed paths (or embeds the exact bytes in the family bundle), so the
old proof remains independently recomputable. A new member of the same family
is impossible after closure, and any post-hoc cross-family selection is
`NOT_ASSESSABLE`.

## 5. Contracts and persistence

### 5.1 Domain contracts

Frozen contracts live in `quantos/contracts/research.py` and include:

- `ResearchQuestionRef`, `ResearchQuestionAlias`, `StrategyLineageRootRef`,
  and `StrategyLineageNodeRef`;
- `DeploymentMandate`, `SelectionScopeRef`, `SelectionScopeAlias`,
  `SelectionScopeOutcome`, `ResearchProgramRef`, and `SelectionDecisionRef`;
- `CodeManifestEvidence`, `CodeManifestPolicyRef`, and
  `ExecutionCodeAttestation`;
- `DataSnoopingDeclaration`;
- `CandidateProposalRecord` and `CandidateIdentity`;
- `PeriodKey` and `EvaluationSeriesReceipt`;
- `CandidateReturnSeries` and `BenchmarkReturnSeries`;
- `CandidateEmissionSeal`;
- `CandidateDisposition`;
- `FamilyFreezeReceipt`, `OosExposureRef`, `OosAccessAttempt`, and
  `OosReleaseReceipt`;
- `FamilyEvaluationBatchReceipt`;
- `TrialLedgerProof`;
- `DataSnoopingFamilyManifest`;
- `RealityCheckResult`;
- `SpaResult`;
- `DataSnoopingAssessment`;
- `DataSnoopingEvidenceRef`, `ArtifactCore`, `ArtifactCoreScanReceipt`,
  `PublishedArtifactEnvelope`, `ArtifactPublicationReceipt`, and
  `ArtifactPublicationReceiptProjection`; and
- `AlphaEvidencePin`, `LegacyAlphaStateBaselineRef`, `AuthorityHeadRef`,
  `ApprovalConsumptionRef`, and `PromotionGuardSnapshot`.

These are separate from `AlphaEvaluation`. Adding fields to historical
`AlphaEvaluation` objects would change existing immutable evaluation hashes and
is explicitly rejected.

The single canonical float/JSON/hash implementation lives in
`quantos/contracts/canonical.py`; services and tools may not carry private
variants. Contract construction enforces NFC strings, finite float-hex values,
positive-zero canonicalization, duplicate-key rejection, and exact LF bytes.

`DataSnoopingAssessment` separates data status from the statistical result:

- `assessment_status`: `ASSESSED` or `NOT_ASSESSABLE`;
- `diagnostic_verdict`: `SUPPORTS_SUPERIORITY`,
  `DOES_NOT_SUPPORT_SUPERIORITY`, `METHOD_DISAGREEMENT`,
  `PENDING_MORE_REPLICATIONS`, `MONTE_CARLO_UNRESOLVED`, or
  `NOT_APPLICABLE`; and
- `promotion_effect`: always `NONE` in Phase A.

The stored result includes all failure reasons. An exception is never converted
to an empty, passed, or partially assessed family.

### 5.2 Experiment authority schema v3 and lifecycle schema v4

Phase A and Phase B are separate deployable migrations. Phase A migrates
transactionally from schema v2 to additive family-authority schema v3 and does
not change lifecycle-state semantics. Phase B later migrates exact v3 to
lifecycle-authority schema v4 and activates the new state chokepoint. Before
adding family tables, the existing compatibility split is changed explicitly:

- read-only `verify_portable_v2_subset` accepts every version `>=2` and verifies
  only the portable-v2 search/evaluation subset, so an old snapshot can remain
  inspectable without authorizing writes to an unknown future schema;
- mutating `_ensure_search_schema` migrates versions below 2 to v2, permits only
  exact known versions 2, 3, or 4, and rejects versions above 4 before any
  write;
- `_ensure_family_schema` migrates exact v2 to v3 under `BEGIN IMMEDIATE`,
  verifies the family-authority subset on exact v3 or v4, and rejects unknown
  versions above 4;
- `_ensure_lifecycle_schema` is unavailable in Phase A, migrates only exact v3
  to v4 under the explicit Phase-B activation transaction, requires exact v4
  thereafter, and rejects every other version for lifecycle writes; and
- every existing `begin/register/finish/insert_evaluation`, search-link,
  portable capture, recovery, and existing lifecycle path must still work after
  v2-to-v3 migration. Only after v4 activation do legacy alpha-state snapshot
  APIs become read/projection-only.

The store remains the live authority and adds these append-only tables:

- `research_questions`;
- `research_question_aliases`;
- `strategy_lineage_roots`;
- `strategy_lineage_nodes`;
- `strategy_config_lineage_bindings`;
- `research_authority_events`;
- `selection_scopes`;
- `selection_scope_aliases`;
- `selection_scope_publishable_decisions`;
- `selection_scope_outcomes`;
- `research_programs`;
- `selection_decisions`;
- `global_oos_exposures`;
- `global_oos_exposure_consumptions`;
- `data_snooping_declarations`;
- `data_snooping_family_searches`;
- `data_snooping_proposals`;
- `data_snooping_hypotheses`;
- `data_snooping_search_emission_seals`;
- `data_snooping_family_freezes`;
- `data_snooping_oos_releases`;
- `data_snooping_oos_access_attempts`;
- `data_snooping_candidate_dispositions`;
- `data_snooping_evaluation_batches`;
- `data_snooping_family_oos_evaluations`;
- `data_snooping_evaluation_receipts`;
- `data_snooping_benchmark_returns`;
- `data_snooping_family_closures`;
- `data_snooping_grid_assessments`;
- `data_snooping_assessments`;
- `artifact_publication_receipts`.

Phase B schema v4 then adds:

- `lifecycle_authority_activation`;
- `legacy_alpha_state_baseline`;
- `alpha_evidence_pins`; and
- `alpha_transition_records`.

Every table has no-update and no-delete triggers. Content hashes bind canonical
JSON, rows link to the previous record hash where order matters, and all foreign
references are reverified on read.

`ResearchQuestionAuthority` is the only issuer of question and lineage
identities. `issue_question` accepts a versioned structured scope, never a
caller ID, and requires the existing audited multi-party human grant for a new
scope; an exact canonical scope returns the existing question. `register_alias`
inserts one globally unique alias hash that can never be remapped.
`register_lineage_root` binds a canonical generator/strategy-class source
manifest to one unique root; `register_lineage_node(parent_id, ...)` always
inherits its parent's root, and `bind_config(config_hash, node_id)` permits each
config hash exactly one root. Search/program APIs accept neither question IDs
nor root IDs directly: they resolve a preapproved structured question and every
candidate config through these registries and fail if either is unknown or
ambiguous. Every issue/alias/root/node/binding action appends a hash-chained
`research_authority_events` row with its grant/audit identity. Creating an
apparently new root for a registered source manifest, descendant, alias, or
config is rejected.

`SelectionScopeAuthority` issues a scope from a structured deployment mandate
and existing audited multi-party grant. The canonical scope excludes questions
and lineage roots and instead binds the portfolio sleeve/capital/risk-budget
authority, market/universe, label/evaluation horizon, benchmark, and cost-policy
class. `selection_scope_aliases` is globally unique and immutable, so renaming
the mandate cannot allocate another scope. Research/search APIs cannot accept a
caller-selected scope ID. This authority cannot prove that colluding trusted
code or human governance mislabeled the same economic mandate as genuinely
different; that remains inside the explicit trusted-code/review threat
boundary, not an unmentioned machine guarantee.

Every `research_programs` row links to exactly one resolved selection scope and
records its question/root provenance; a publishable family may link multiple
programs and freezes the complete question/root sets. The verifier reloads the
complete scope/question/alias/lineage event chains before accepting a decision.
`selection_scope_publishable_decisions` has unique `selection_scope_id` and
`selection_decision_id` columns, so exactly one decision can reserve a scope's
publishable slot. `selection_scope_outcomes` has one unique row per scope and
permits only `WINNER_PINNED` with an exact alpha/evidence binding or
`CLOSED_WITHOUT_PROMOTION` with no winner; diagnostic OOS access anywhere in the
scope requires that row. `selection_decisions` has an authority-owned monotone
ID unique within `selection_scope_id` and exactly one unique `family_id`.
`global_oos_exposures` has globally unique `oos_exposure_id` and canonical
exposure-fingerprint constraints. Availability is the absence of a consumption
row; the CAS is one guarded append-only insert into
`global_oos_exposure_consumptions`, whose unique `oos_exposure_id` constraint
permits exactly one consuming decision/family/access-attempt tuple. It can never
be updated or deleted. A migration may create the new empty tables; it may not
infer questions, aliases, lineage roots/nodes, config bindings, selection
scopes/aliases/outcomes, programs, decisions, exposures, declarations,
dispositions, return series, evidence pins, or assessments for old rows.

The Phase-A v2-to-v3 migration neither adds `alpha_state.state_version` nor
changes any alpha-state write API. It may be deployed, exercised, and rolled
back independently as a diagnostic family authority while current lifecycle
behavior remains byte-for-byte compatible. Phase-A code cannot instantiate a
Phase-B evidence verifier or claim that a family assessment has lifecycle
effect.

The Phase-B v3-to-v4 migration requires an exclusive ExperimentStore writer
lease and proof that research-runner, promotion, maintenance, and recovery
writers are stopped. One `BEGIN IMMEDIATE` transaction hashes the exact
pre-cutover `alpha_state` snapshot, adds
`alpha_state.state_version INTEGER NOT NULL` initialized to `0`, creates the
pin/transition tables and all constraints/triggers, copies each exact
pre-cutover alpha ID/state into append-only `legacy_alpha_state_baseline` rows,
binds their ordered content hash to the activation record, and inserts the unique
`lifecycle_authority_activation` row last. That row binds the pre-cutover state
hash, schema/code-manifest/policy versions, activation timestamp, writer-lease
identity, and migration transaction hash. Any failure rolls back the entire
schema and marker; there is no partially active v4.

Deployment starts v4-aware writers only after that marker commits. Every
writer first verifies exact schema v4, the activation row, and the deployed code
manifest; a stale v3 process or a process that held no activation writer lease
is rejected before state mutation. New writes CAS the exact expected version
and increment by one. The append-only hash-chained
`alpha_transition_records` authority contains source and destination state,
actor/audit payload, evidence-pin hash when required,
selection-scope-outcome hash, approval-consumption ID/hash for `APPROVED`,
transition ID, timestamp, and previous record hash. A partial unique constraint
on non-null approval-consumption hash makes one burned grant usable by at most
one transition. Historical in-memory lifecycle audit entries are not backfilled.
Therefore an advanced legacy state may be read as history but cannot satisfy a
new positive transition without the required persisted pin/transition
authority.

`insert_alpha_initial` is the only row-creation seam and inserts `DRAFT` with
`state_version=0`. Thereafter every authoritative state change, positive or
non-positive, uses a versioned transition primitive that appends
`alpha_transition_records` and its outbox in the same transaction; positive
wrappers add their evidence/pin/outcome requirements. Legacy
`save_catalog_snapshot` and `put_alpha` may update non-state projection fields
only when the supplied state and version exactly equal the stored row. They
reject an existing-row state/version change and cannot upsert around the
transition seam. Governance tests inspect public APIs and SQL write sites so no
service can update `alpha_state.state` or `state_version` outside the locked
transition primitives.

The family-authority verifier accepts exact schema v3 or v4 and validates its
entire family subset, including every table, column, trigger, hash, and
cross-reference. The lifecycle/promotion verifier additionally requires exact
v4 plus the valid activation row and transition authority. The separate live
trial-ledger `>=2` verifier intentionally checks only its portable-v2 subset and
cannot authorize family assessment. Damage confined to a v3 family table may
leave portable-v2 capture readable, but family closure, assessment, export, and
promotion verification all fail closed.

`data_snooping_benchmark_returns` contains exactly one immutable, complete
benchmark gross/cost/net/turnover receipt per family. Its ordered period keys
must equal every candidate series byte-for-byte after canonical encoding and
its cost decomposition must reconcile under the same policy. A benchmark
definition/hash without that authoritative period series is insufficient and
makes the family `NOT_ASSESSABLE`.

Opening, proposal registration, linking, freezing, OOS release, evaluation,
closing, assessing, and alpha transitions use explicit store transactions. A
search ID is unique across family links and cannot be linked after it begins.
Insert-guard triggers reject new links/proposals/hypotheses after freeze and
reject dispositions, receipts, or benchmark rows after closure.

Transaction composition is explicit: existing public store methods remain
transaction owners, but their SQL bodies are first extracted into private
`_insert_*_locked(conn, ...)` / `_transition_*_locked(conn, ...)` primitives.
Locked primitives require the caller's active `BEGIN IMMEDIATE`, never execute
`BEGIN`, `COMMIT`, or `ROLLBACK`, and never open a second connection. Public
single-operation methods wrap exactly one primitive; the whole-family batch and
alpha-pin transitions compose multiple locked primitives under one outer
transaction. Tests trace SQL transaction control and fail on nested `BEGIN`,
partial commits, or a primitive called without the store lock. This refactor is
required before either composite transaction is introduced.

`FamilyOosEvaluationService.begin_evaluation_batch` is the only OOS-read seam.
It first obtains and verifies the declaration-identical
`ExecutionCodeAttestation` and immutable-artifact lease. In one
`BEGIN IMMEDIATE` transaction it then inserts the whole-family `STARTED` access
attempt with those hashes and CAS-consumes the globally unique exposure against
the exact expected version, decision, family, config-set digest, and batch ID.
The data authority checks that committed row and the still-live exact lease
before returning any byte. A failed CAS, second attempt, reused fingerprint,
sibling exposure conflict, missing/mutable execution provider, mismatched
attestation, broken lease, or uncommitted attempt releases no data. The sole
publishable decision may perform this first access before a selection-scope
outcome exists; every diagnostic sibling must first prove the immutable
`WINNER_PINNED` or `CLOSED_WITHOUT_PROMOTION` outcome.

The service computes against an immutable catalog/universe/input snapshot
through a new pure detailed-evaluation function. It returns the legacy
`AlphaEvaluation` view plus gross/cost/net/turnover series without mutating the
shared `AlphaCatalog`, lifecycle state, or any store. All candidate calculations
finish in isolated memory before the one batch transaction. The ordinary
single-candidate evaluator may retain its legacy projection behavior outside
this path, but the blind family service cannot call a method that writes the
catalog while iterating candidates.

`FamilyOosEvaluationService.commit_evaluation_batch` is the only successful OOS
write seam. While retaining the same immutable-artifact lease, it obtains the
terminal `ExecutionCodeAttestation`, requires exact equality with the STARTED
attestation/runtime manifest, and rechecks the live lease inside the store
transaction. A failed terminal check uses the irreversible INCOMPLETE path and
commits no return receipt. In one later `BEGIN IMMEDIATE` transaction it commits every
immutable candidate `AlphaEvaluation` payload through the extracted
`_insert_evaluation_locked` primitive into the existing `evaluations` authority,
with its existing hash-chained `evaluation_audit_outbox` intent. It also inserts
the dedicated `data_snooping_family_oos_evaluations` link from
family/proposal/config to exact evaluation run/content hash, the one family
batch receipt, all candidate receipts, strict period keys, gross returns,
per-period costs, turnovers, net returns, benchmark receipt, all duplicate
proposal dispositions, the start/terminal execution-attestation and lease
hashes, and the `COMPLETED` attempt record. The lease is released only after
that transaction commits. The batch is
all-or-nothing; per-candidate success commits are forbidden. Family OOS links
reference the sealed source search/proposal/config rows but never insert into or
mutate `search_evaluations`: every linked search is already terminal before OOS
release. Existing search/inner-validation evaluations and the evaluation-audit
verifier keep their legacy authority unchanged.
It verifies for every period that the float64 operation `gross - cost` produces
the stored net value exactly after positive-zero canonicalization. The
cost-model ID/parameters, dynamic cost inputs, and their hash are part of every
receipt. Mutable `AlphaCatalog.set_returns`, a later catalog read, or a rerun
backtest is never an authority source for family evidence; any user-facing
catalog projection is rebuilt only after commit from the authoritative batch
and is not part of promotion verification.

Once `STARTED` commits, the exposure is consumed even if the evaluator crashes.
An exception path uses one fail-closed finalization transaction to append the
attempt's `INCOMPLETE` terminal record and a
`PERFORMANCE_ATTEMPTED_INCOMPLETE` disposition for every `CONFIG_ACCEPTED`
proposal, including every duplicate. On process restart, recovery performs that
same transaction for every orphan `STARTED` attempt before accepting work.
Because a hard kill provides no trustworthy per-candidate progress, all accepted
proposals are marked incomplete unless the complete batch transaction committed;
pre-evaluation-invalid proposals retain their existing dispositions. Until
recovery, `STARTED` itself blocks closure. There is no retry, lease takeover,
replacement exposure, or partial-result salvage. Closure accepts exactly one
completed attempt and one complete batch, or becomes `NOT_ASSESSABLE`.

The official evaluation chokepoint refuses an unregistered proposal/config,
an unfrozen family, an exposure not reserved for this decision, a missing
committed access attempt, a second/previously consumed handle, or a calendar/cost
mismatch. The governance boundary forbids search strategies from calling the
evaluator around that chokepoint. Closure fails if an emission seal
is missing or disagrees with registered proposals, if the declared stopping
rule is unmet, if a linked search is non-terminal, if a proposal lacks one
valid disposition, if an accepted config lacks one byte-consistent receipt, or
if any immutable hash does not match. Reopening or overwriting a frozen/closed
family is impossible.

`data_snooping_grid_assessments` stores one immutable local result for each
declared block-length/replication-count pair, unique on `(family_id,
block_length, replication_count, algorithm_version)` and chained in declared
schedule order within each block length. Each larger row must prove the
previous index stream is its exact prefix. `data_snooping_assessments` is a
separate one-row-per-family conclusion authority; it is never a favorable grid
row relabeled as the family result. The conclusion is derived only from the
latest row of every declared block length after every required checkpoint is
terminal, using the preregistered any-grid disagreement and maximum-p rules.
Otherwise unresolved grid points advance to the next declared count. If the
schedule ends with any primary-kernel ambiguity, the family conclusion is
`MONTE_CARLO_UNRESOLVED`; kernel/data failures produce a unique
`NOT_ASSESSABLE` conclusion. Once the family conclusion exists, inserting any
later grid row is rejected. A `DataSnoopingEvidenceRef` can name only that
automatically derived family-conclusion hash; it cannot name one favorable
block length, `PENDING_MORE_REPLICATIONS`, or an operator-selected run.

`ExperimentStore.commit_manifest_winner_to_testing` is the only authoritative
`DRAFT -> TESTING` seam. It is called only by
`DeferredFamilyPromotionService` after the family is frozen, completely
evaluated, closed, and has an effective assessment. In one `BEGIN IMMEDIATE`
transaction it CAS-checks the exact manifest winner's `DRAFT` state/version,
reloads the scope/family/closure/winner evaluation and every assessment-grid
row, and deterministically rederives the aggregate result under the pinned
algorithm and policy. The exact required predicate is
`assessment_status=ASSESSED`,
`diagnostic_verdict=SUPPORTS_SUPERIORITY`, every preregistered White-RC and
SPA-consistent grid point is an uncertainty-resolved `REJECT`, and the
replication schedule and promotion-eligibility policy are satisfied. It then
confirms no outcome/sibling-OOS race exists, runs the existing testing-transition
guard, increments `state_version`, appends the hash-chained transition/audit
record, and writes the outbox event. A non-winner, incomplete/non-assessable
family, any other diagnostic verdict, ambiguous/disagreeing/non-rejecting grid,
insufficient or policy-ineligible replication, mismatched evaluation, or old
manager route is rejected. Non-selected candidates created by this family and
still in DRAFT remain research-only DRAFT records. Any pre-existing non-winner
state, including TESTING, REJECTED, VALIDATED, PAPER_TRADING, APPROVED, or
RETIRED, remains byte-for-byte unchanged and receives no pin or positive
authority from this family; no candidate borrows the winner's state.

A crash before this transaction leaves `DRAFT`; a crash after it leaves the
winner durably `TESTING`, from which the same deferred service may attempt the
separate validation-pin transaction. The two transitions are intentionally
separate audited state-machine edges. Failure of the second never rolls back or
hides the first, never permits sibling OOS before a scope outcome, and may be
resolved only by a later fresh guard attempt or by closing the selection scope
without promotion.

An exact manifest winner that the v4 activation baseline records as already
`TESTING` is not forced through a fabricated DRAFT transition.
`ExperimentStore.commit_legacy_testing_winner_adoption` is the sole adoption
seam. In one `BEGIN IMMEDIATE` transaction it requires the immutable baseline
row to be `TESTING`, the current row to remain `TESTING` at `state_version=0`,
the exact activation-snapshot hash, no scope outcome/pin/sibling-OOS race, and
the same rederived positive family predicate and testing guards above. It then
increments the version and appends a unique hash-chained
`LEGACY_TESTING_ADOPTED_FOR_FAMILY` TESTING-to-TESTING transition/audit/outbox
record binding that family and baseline; it creates no evidence pin or
`WINNER_PINNED` outcome. This explicit no-state-change event preserves history
and is the only pre-cutover TESTING predecessor accepted by the validation-pin
seam. A post-activation/unrelated TESTING state or any baseline state other than
TESTING cannot be adopted for promotion.

`ExperimentStore.commit_validated_transition_with_evidence_pin` is the only
non-human `TESTING -> VALIDATED` persistence seam. In one `BEGIN IMMEDIATE`
transaction it CAS-checks the alpha's expected state/version, reloads and
verifies the family ref and exact manifest-winner evaluation, reloads every
assessment-grid row, and repeats the same exact `ASSESSED` plus
`SUPPORTS_SUPERIORITY` plus uncertainty-resolved every-grid RC/SPA and
replication/policy-eligibility derivation used by the DRAFT-to-TESTING seam. It
also requires that the immediately preceding family-bound authority record is
either this family's exact `DRAFT -> TESTING` transition or its exact
`LEGACY_TESTING_ADOPTED_FOR_FAMILY` marker, and that the selection scope still
has no outcome. Only then does it
insert one
append-only `alpha_evidence_pins` row, CAS-updates versioned `alpha_state`,
inserts the hash-chained `alpha_transition_records` domain/audit row, inserts
the canonical transition outbox event, and inserts the unique
`WINNER_PINNED` `selection_scope_outcomes` row binding the same alpha/ref. The
transaction requires the evidence's selection-scope history head to equal the current
tip and rejects any pre-existing outcome or sibling OOS release. The pin binds alpha
ID, family/decision/program/scope IDs, effective assessment hash, closure-manifest
hash, exact winner config/evaluation hash, source-code manifest, verifier-policy
version, `PromotionGuardSnapshot` hash, and transition ID. Any
insert/CAS/domain-record/outbox failure rolls
back all of it.
The state row cannot advance while the pin or selection-scope outcome is absent, and
neither may exist without that exact transition.

Guard evaluation is transaction-owned, not a precomputed caller step. Under the
same ExperimentStore `BEGIN IMMEDIATE`, the method reloads the exact evaluation,
recomputes Deflated-Sharpe from the current global DISTINCT-config trial count,
recomputes the current BHY population/survivor set and population head, and runs
every store-derived lifecycle guard. Leakage, PBO/CPCV, regime, research-risk,
paper, model-card, and approval inputs arrive only as immutable versioned refs
with authoritative content/head hashes; caller booleans or mutable objects are
rejected. A fixed-order `PositiveTransitionCoordinator` holds stable read/head
leases on those local owner authorities through the ExperimentStore commit and
fails closed if any authority cannot provide a stable verified head. The
canonical guard inputs, current trial count, BHY head/survivor decision, every
external authority head, and final guard-decision hash are stored in
`PromotionGuardSnapshot` and bound by `alpha_transition_records`.

`ExperimentStore.close_selection_scope_without_promotion` is the only other
selection-scope-outcome seam. In one `BEGIN IMMEDIATE` transaction it CAS-checks that
the scope has no outcome, this family/scope has no alpha evidence pin or
validated/later positive transition, no concurrent `TESTING -> VALIDATED` CAS,
and no `WINNER_PINNED` transition, and it verifies the expected exact
selection-scope history tip. It reloads and binds the winner's current
state/version plus its v4 activation-baseline row when present. Closing is total
for an otherwise unconsumable winner: DRAFT, this-family TESTING, unrelated or
legacy TESTING, REJECTED, VALIDATED, PAPER_TRADING, APPROVED, RETIRED, or another
known pre-existing state may all close the scope when no positive transition or
pin was created by this family. Existing alpha history is never rewritten and a
pre-existing advanced state gains no authority from the closing family. The
transaction then inserts the unique
`CLOSED_WITHOUT_PROMOTION` outcome plus its audit/outbox event. It cannot be
reopened or converted to `WINNER_PINNED`. The winner remains in its observed
state for historical truth, while this family ref becomes permanently
non-promotable; any later alpha change must use an independently valid ordinary
transition. A diagnostic sibling's OOS-access CAS accepts only one of the two
verified outcomes and otherwise releases no data.

Every later `-> PAPER_TRADING` and human `-> APPROVED` transition uses the same
store authority and reloads that exact append-only pin, current family
verification, and exact winner evaluation; latest-evaluation lookup and a new
caller-supplied ref are forbidden. After `WINNER_PINNED`, later diagnostic
sibling rows may extend the selection-scope chain without invalidating historical
verification or that already locked winner; they can never change the outcome,
alpha, or ref. A ref that was not pinned before the selection-scope tip advanced cannot
be newly consumed. Existing/legacy rows already in `VALIDATED`,
`PAPER_TRADING`, or `APPROVED` without a valid pin are not grandfathered or
backfilled. Reads and every further positive transition classify them as
`LEGACY_EVIDENCE_PIN_MISSING` and fail closed permanently for that alpha
identity. A new governed family cannot retrofit or adopt an already advanced
legacy row; only a genuinely new config/alpha identity may traverse the full
governed path. This migration rule does not demote or rewrite history and does
not block ordinary de-risking; it makes the legacy state non-authoritative for
all further positive advancement.

`ExperimentStore.commit_positive_transition_with_pinned_evidence` is the only
later positive-state seam, including `VALIDATED -> PAPER_TRADING` and the final
human `-> APPROVED`. In one `BEGIN IMMEDIATE` transaction it CAS-checks expected
state and `state_version`, reloads the exact pin/scope outcome/family/winner
evaluation and required human grant, increments the state version, appends the
hash-chained transition/audit record, and writes the outbox event. Any mismatch,
concurrent maintenance/rejection/promotion, injected failure, or stale request
rolls back all state/domain/outbox writes. A read-then-write lifecycle call or a
request-ID-only lock is not sufficient. This method repeats the same
transaction-owned current trial-count/BHY recomputation and immutable external
authority-head verification, creates a new `PromotionGuardSnapshot` for this
transition, and binds its hash into the transition record; an earlier guard
decision cannot be replayed at a later state.

Final human approval preserves the existing fail-closed burn-first protocol.
After all non-mutating prechecks and fixed-order single-flight acquisition, the
official executor first uses `ApprovalStore`'s CAS to persist the exact request
as `CONSUMED`, binding subject alpha, `-> APPROVED` action, executor identity,
expected alpha state/version, evidence-pin hash, and a unique consumption ID.
Only that committed `ApprovalConsumptionRef` and its verified authority head may
enter `commit_positive_transition_with_pinned_evidence`; an `APPROVED` but
unconsumed request or caller-built grant is rejected. The resulting
`alpha_transition_records` row contains the exact consumption ID/hash, with a
unique constraint preventing any second transition use.

The ApprovalStore and ExperimentStore are intentionally not compensated as one
distributed transaction: a failure or kill after consumption but before the
state commit leaves the request burned and requires a new approval request. A
kill before consumption burns nothing; a kill after the ExperimentStore commit
is recovered by reading the unique transition/consumption binding, not by
reusing the grant. Two workers racing the same request can produce at most one
consumption and one transition. `execute_promotion` is migrated to this order
and no longer calls the in-memory lifecycle manager plus `registry.save` as a
state authority; projections reload from the committed ExperimentStore row.

### 5.3 Published evidence artifact

Phase A provides a deterministic exporter and verifier. The exporter writes a
canonical UTF-8/LF `PublishedArtifactEnvelope` under a content-addressed path:

```text
evidence/data_snooping/sha256-<payload-hash>.json
```

For an `ASSESSED` family, the embedded `ArtifactCore` is self-contained for
statistical recomputation and authority verification: it contains the
declaration, authority-issued selection-scope/mandate/alias and complete
program/question/lineage-root/decision/exposure identities, sole-slot
reservation/outcome, verified scope sibling/exposure-history head,
freeze/OOS-release and one-shot access-attempt
receipts, whole-family evaluation-batch receipt, and closure manifest. For each
non-quarantined envelope it contains hash/length/media type, DLP policy/result
receipt, and safe canonical accepted-proposal bytes/hashes when present. For each
quarantined row it contains only the safe proposal ID, opaque quarantine-record
ID, length bucket, media type, policy, and fixed reason. It also contains
candidate dispositions, all four independently derived counters, ordered period
keys, exact benchmark and candidate
gross/cost/net/turnover float64 series encoded with `float.hex()`, every staged
block-grid assessment, exact authority-row hashes, immutable content-addressed
trial-ledger proof, and software/algorithm versions. It deliberately excludes
all opaque raw-envelope octets and restricted blob paths; invalid raw bytes are
not needed to recompute RC/SPA. A `NOT_ASSESSABLE` payload likewise contains
only safe immutable metadata and the exact deficiency list, never invented
returns or quarantined bytes.

Publication uses an explicitly acyclic two-layer format. First the exporter
canonicalizes `ArtifactCore`, whose declaration already binds the DLP policy and
scanner version but which contains no result for scanning itself. It scans those
exact core bytes and creates a fixed-schema `ArtifactCoreScanReceipt` containing
the core hash, policy/version, allow/deny status, and canonical result hash. On
ALLOW only, it canonicalizes `PublishedArtifactEnvelope = {core,
core_scan_receipt}`. It then scans the exact final envelope bytes. A scanner hit,
unavailable scanner, policy/version mismatch, or unscanned nested field aborts
the write.

The final-envelope scan result is not embedded back into the envelope. Instead,
after ALLOW the exporter atomically appends an
`artifact_publication_receipts` authority row containing the final envelope
hash, policy/version, allow status, result hash, exporter event/head, and target
content-addressed path; it then writes a separately canonical, content-addressed
fixed-schema `ArtifactPublicationReceiptProjection` sidecar. The sidecar has no
free-form or opaque byte field. It contains the complete canonical publication
row, authority-instance lineage hash, exact receipt schema/policy version, and
the complete canonical append-only publication-receipt chain prefix from that
authority's genesis through this row, including every previous/current row
hash and chain head. Its own content hash and path are computed only after this
fixed projection is complete. The main filename hash covers only the final
envelope bytes; the sidecar has its own hash and is not referenced from the
envelope. No object contains its own hash or final-scan result, so no fixed
point is required.

The runtime verifier requires the live `artifact_publication_receipts` row and
recomputes its full live chain. The clean-checkout verifier instead requires the
exact content-addressed `ArtifactPublicationReceiptProjection`, recomputes every
canonical row hash and previous-head link from genesis, verifies the authority
lineage/schema/policy and terminal head, and requires the terminal row to bind
the exact envelope hash/path and ALLOW result. A detached row hash, truncated
prefix, foreign authority instance, or envelope/sidecar mismatch fails closed.
Redacting and then publishing a hit is forbidden. Both paths receive Git
`text eol=lf` rules when introduced. A `NOT_ASSESSABLE` core contains the
available immutable provenance and exact deficiency list only when both safe
layers independently pass the same publication process.

An `ASSESSED` artifact is publishable only with a clean commit, at least 10,000
replications per block-grid point, verified blind-OOS release, and 100% proposal
disposition/eligible-config return coverage. A `NOT_ASSESSABLE` artifact
may be published to make the missing evidence explicit, but cannot be counted
as statistical capability evidence. The portable trial-ledger snapshot remains
its existing exact schema-v2 contract; the family artifact binds to it rather
than silently changing its five-table schema.

The official exporter also embeds a `DataSnoopingAuthorityProjection` captured
from one read transaction over the verified schema-v3-or-v4 family authority.
The projection
contains the selection-scope deployment-mandate/alias rows and heads, sole-slot
reservation and outcome, complete linked program/question/lineage-root sets,
scope-history head, decision/exposure/family rows, and chain heads needed to
recompute this artifact, plus the authority-instance lineage hash and exact
schema/trigger catalog. It is part of the content-addressed payload rather than
a mutable `data/cache` path. Its seal hashes the exact canonical projection
bytes and the live chain heads observed in that transaction. It contains no
credentials and grants no mutation or promotion authority.

Runtime lifecycle verification always reloads the live ExperimentStore and
requires every projected row and chain head to match the same exact live-store
prefix byte-for-byte. It then verifies the entire live suffix. The only allowed
suffix contains `DIAGNOSTIC_ONLY` sibling activity after the same immutable
selection-scope outcome; any second publishable slot, changed outcome/ref/alpha,
pre-outcome sibling OOS access, or other invariant violation fails closed. The
offline top-tier
checker in a clean checkout may instead verify the content-addressed projection
and artifact together under the repository's Git-review provenance boundary.
It reports PARTIAL when neither a live authority nor a valid official
projection is available. A projection can demonstrate that the governed
statistical control ran; it can never authorize a lifecycle transition, replace
the runtime CAS, or claim external attestation. This is the same trusted-code
boundary already stated for other L2 unkeyed evidence seals.

### 5.4 Ownership and import boundaries

- `quantos/contracts/research.py` owns frozen research contracts;
  `quantos/contracts/canonical.py` owns the only canonical byte/hash codec.
- `services/validation/reality_check.py` owns the pure RC/SPA/bootstrap kernel;
  `services/research/validation/reality_check.py` is compatibility export only.
- `services/research/data_snooping/family.py`, `assessment.py`, and
  `evidence.py` own family closure, assessment scheduling, and artifact
  verification/export respectively. They do not own SQL or lifecycle state.
- `services/research/data_snooping/program_authority.py` owns structured
  selection-scope/deployment-mandate and research-question issuance,
  alias/lineage resolution, and program-link policy; persistence remains in
  ExperimentStore and callers cannot inject IDs.
- `services/research/experiment_registry/store.py`, `registry.py`, and
  `services/research/ports/persistence.py` own schema/API persistence seams.
- `services/intelligence/family_search.py` owns declaration, search/freeze, and
  blind-OOS orchestration but cannot own a positive lifecycle transition.
- `services/alpha/lifecycle.py`, `guards.py`, and `promotion.py` own the injected
  authority guard and same-ref re-verification; a small
  `services/alpha/family_promotion.py` may host
  `DeferredFamilyPromotionService`.
- `tools/data_snooping_evidence.py` is a thin offline CLI. No `services/` module
  imports `tools/` or invokes Git/network/Docker/broker code.
- Git commit/blob capture lives at the tools/deployment boundary. It emits an
  immutable `CodeManifestEvidence` to a pure injected verifier; service modules
  neither shell out to Git nor reinterpret worktree bytes. Offline verification
  uses the exact committed blobs embedded in the content-addressed bundle,
  while runtime promotion receives the deployed immutable-artifact manifest
  from its deployment boundary and fails closed when that provider is absent.
- `config/code_manifest_policy.json` is the authority-owned
  `CODE_MANIFEST_POLICY_V1`; `services/governance/code_manifest.py` verifies its
  canonical roots, transitive/dynamic closure evidence, exact path-set hash, and
  blob hashes without maintaining a second caller-controlled root list.
- `scripts/assess_top_tier.py` consumes the semantic evidence verifier rather
  than implementing a second statistical/hash path.

### 5.5 Machine definition of a real governed family

Top-tier credit uses the closed predicate `REAL_GOVERNED_FAMILY_V1`; neither a
filename nor a caller-provided `real=true` field is accepted. The semantic
verifier returns true only when all of the following are true:

1. The configured project research authority issued the
   `selection_scope_id`, deployment-mandate/alias chain, all linked
   program/question/lineage-root IDs, monotone `selection_decision_id`, family
   ID, and `oos_exposure_id`. The scope-level sole-publishable-slot reservation,
   exact outcome when present, frozen question/root sets, append-only rows, and
   authority-instance lineage hash exist in the verified live ExperimentStore
   for runtime use, or in the exact official authority projection for offline
   capability assessment, and use immutable
   `execution_class=GOVERNED_RESEARCH`. `TEST_FIXTURE`, `DEMO`,
   `MANUAL_IMPORT`, and unknown classes are ineligible. Top-tier credit also
   requires exact schema v4, a valid lifecycle-activation/baseline projection,
   and the Phase-B chokepoints even when the honest family outcome is
   `CLOSED_WITHOUT_PROMOTION`.
2. The decision was opened through the official persistent family orchestrator,
   is the selection scope's sole publishable decision, includes every frozen
   linked program/question/root, has at least one linked whitelisted engine
   search run, and was not created by
   the historical-audit/import CLI. External manual proposals may be registered
   inside that official family to preserve completeness, but a manually
   assembled/imported family or assessment is never a real-family authority.
3. Every search-data and OOS dataset handle resolves through the data catalog to
   immutable point-in-time observed provenance. Any dataset marked
   `SYNTHETIC`, `FIXTURE`, `DEMO`, `MOCK`, `MANUAL_SERIES`, or unknown is
   ineligible. A real family cannot depend on files under test/fixture/demo
   asset roots or on an operator-supplied return matrix.
4. The scope mandate/alias chain, sole-slot reservation/outcome, complete
   scope-history suffix, global exposure CAS/access-attempt, whole-family batch,
   sibling history, four counters, closure, assessment, declaration-identical
   start/end `ExecutionCodeAttestation` plus unbroken immutable-artifact lease,
   code manifest, DLP receipts, publication authority/sidecar, and artifact all
   pass the same authority verifier; `assessment_status=ASSESSED`, publishable
   replication policy, and `unique_performance_hypothesis_count >= 2` are
   required. `NON_PUBLISHABLE_TEST_RUN` is ineligible regardless of p-value.
5. The content-addressed artifact recomputes byte-for-byte from those authority
   rows and RC/SPA recomputation matches. A fixture, demo, manual export, or
   semantically valid but authority-detached payload cannot satisfy the
   predicate.

The execution class and dataset provenance enums are assigned by their owning
authorities when rows/handles are issued, not copied from an evidence payload.
The top-tier checker opens the configured project authority read-only when it
exists; otherwise it verifies the official content-addressed authority
projection and calls this predicate. It never trusts a detached payload field.
Tests use a separately marked authority instance and therefore cannot raise the
score. This is a machine admission rule within the existing trusted-code/process
threat boundary, not a claim of external attestation.

## 6. Phase A behavior

The new pure kernel is implemented at:

- `services/validation/reality_check.py`; and
- the compatibility export `services/research/validation/reality_check.py`.

The diagnostic orchestration reads only the ExperimentStore family authority,
computes RC and all three SPA variants, stores the immutable assessment, and
optionally exports its content-addressed evidence artifact. It does not import
alpha lifecycle managers, write the alpha catalog, alter evaluator verdicts, or
advance any state.

Running Phase A's **historical audit command** against the currently known 39/8
material must emit a non-authority `NOT_ASSESSABLE` audit artifact with explicit
missing-disposition/return coverage. It must not create family-authority rows or
an EvidenceRef, manufacture 31 return series, merge incompatible dataset
versions, or improve a top-tier score merely because the kernel exists.

The current console-oriented `services/data/catalog.py` synthetic catalog does
not satisfy `REAL_GOVERNED_FAMILY_V1`. A real run must resolve observed,
point-in-time dataset handles through the data authority with immutable quality,
version, universe, and knowledge-time receipts. Until that adapter and at least
one genuinely governed family exist, Phase A/B code completion must leave the
top-tier requirement PARTIAL or FAILED rather than relabeling synthetic data.

## 7. Phase B deferred-promotion policy

Phase B is a separate implementation plan because the current
`ResearchAgentLoop` advances each passing candidate before a multi-round search
family is complete.

`DeferredFamilyPromotionService` becomes the only non-human positive-transition
orchestrator. `ResearchAgentLoop` may generate/evaluate and may de-risk to
`REJECTED`, but it may neither import `AlphaLifecycleManager` nor call a positive
transition directly. A governance test scans every `services/intelligence`
module and rejects any route around the service.

The lifecycle is also hardened at the lower chokepoint:

- every `TESTING -> VALIDATED` transition requires an opaque
  `DataSnoopingEvidenceRef` and an injected `DataSnoopingEvidenceAuthority`;
- absence of the verifier/ref, a caller-built result/boolean, or any mismatch is
  a guard rejection;
- successful verification invokes the single ExperimentStore CAS transaction
  that atomically inserts the append-only alpha evidence pin, state persistence,
  transition/audit row, and transition outbox event; no in-memory state change
  precedes that commit;
- every later `-> PAPER_TRADING` and final human `-> APPROVED` transition reloads
  and reverifies that exact pin and closure-winner evaluation rather than
  whichever unrelated evaluation happens to be latest; and
- legacy advanced states without a valid pin fail closed and cannot be
  grandfathered, inferred, or backfilled from mutable catalog data.

The official persistent search path will change as follows:

1. Have the authority issue the deployment-mandate-bound selection scope, its
   complete program/question/strategy-root set, the selection decision/family,
   and globally unique OOS exposure; preregister the declaration,
   sibling-history head, sole publishable slot, separate search/OOS data
   contracts, budgets, external-proposal deadline, block grid, and code manifest
   before search.
2. Generate/register all raw proposals using search/inner-validation data only;
   no OOS handle exists in the generator principal.
3. Finish every linked search, verify emission seals, ingest all in-scope manual
   proposals, freeze the family, and revoke generator handles.
4. Before releasing any OOS byte, commit the whole-family access attempt and
   CAS-consume the global exposure. Evaluate once, then atomically commit the
   entire candidate/benchmark batch and dispositions. Any crash or attempted
   but incomplete batch permanently consumes the handle and parks the family as
   `NOT_ASSESSABLE`; retry is forbidden.
5. Capture immutable content-addressed trial-ledger proof, then close the family
   against that proof and the complete receipts.
6. Compute/persist the full block-grid assessment and derive the one effective
   `DataSnoopingEvidenceRef`; caller booleans such as
   `data_snooping_ok=True` are forbidden.
7. Through `DeferredFamilyPromotionService`, consider only the
   manifest-selected winner. First call the unique
   `commit_manifest_winner_to_testing` seam for a DRAFT winner, or the explicit
   `commit_legacy_testing_winner_adoption` seam for an unchanged pre-cutover
   TESTING winner; every other existing state is non-promotable and the scope
   must take the audited no-promotion outcome. Then call
   `commit_validated_transition_with_evidence_pin` for `TESTING -> VALIDATED`,
   atomically persisting the exact evidence pin/state/transition/outbox and the
   `WINNER_PINNED` scope outcome. Every seam rederives the exact positive
   aggregate RC/SPA verdict and current promotion eligibility. Later
   `-> PAPER_TRADING` and human `-> APPROVED` transitions reload that same pin
   through the transactional positive-transition seam and persist a fresh
   `PromotionGuardSnapshot`. Every existing DISTINCT-config DSR count, BH-FDR,
   leakage, regime, PBO/CPCV, risk, and actor guard must still pass.

For each method and block-grid point, classification is mechanical:
`AMBIGUOUS` when `abs(p_plus - alpha) <= mc_radius_2se(B)`, `REJECT` when
outside that radius and `p_plus < alpha`, and `NON_REJECT` otherwise. Equality
to alpha is necessarily ambiguous because the radius is positive. The rows
below are evaluated in the order shown. In particular, once uncertainty is
resolved, disagreement at **any** grid point has priority over aggregate
maximum-p or support/no-support summaries:

| Condition | Family result | Promotion |
|---|---|---|
| Missing, incomplete, stale, tampered, incompatible, fewer than 10,000 replications, or invalid variance | `NOT_ASSESSABLE` | fail closed |
| Evidence names a candidate other than the manifest winner | invalid reference | fail closed |
| Either primary result is `AMBIGUOUS` at any grid point and a declared larger replication count remains | `PENDING_MORE_REPLICATIONS` | advance all required grid streams to the next declared prefix; no EvidenceRef |
| Either primary result is `AMBIGUOUS` at any grid point after the declared schedule is exhausted | `MONTE_CARLO_UNRESOLVED` | non-promotable |
| At any grid point White RC is `REJECT` and SPA-consistent is `NON_REJECT`, or vice versa | `METHOD_DISAGREEMENT` | human review marker, but non-promotable |
| Both primary methods are `REJECT` at every block-grid point | `SUPPORTS_SUPERIORITY` | selected winner only may continue through existing gates |
| No method disagreement exists, but at least one grid point has both primary methods `NON_REJECT` | `DOES_NOT_SUPPORT_SUPERIORITY` | no candidate may advance; this includes block-grid sensitivity |

Human review cannot turn disagreement or missing evidence into a pass. It may
authorize a new preregistered family/evaluation, not mutate the old assessment.
Non-selected candidates created by this family and still in DRAFT remain
visible as research-only records with a non-promotable family disposition.
Every pre-existing non-winner state remains unchanged. None can use the selected
winner's evidence.

RC/SPA rejects an omnibus family null; it does not by itself prove that the
selected winner is individually superior. The winner gains only permission to
continue through the existing gates and natural-forward paper track. An
individual-superiority claim would require a separately specified stepdown
procedure or untouched forward holdout and is outside this design.

The ordinary threat-model boundary remains explicit: immutable store lookup and
the official orchestration path constrain non-human agents, while trusted code
inside one process can still call lower-level lifecycle objects directly. Tests,
the governance scanner, code review, and Phase 14 process isolation govern that
build-time surface.

## 8. Testing strategy

Implementation is tests-first.

### 8.1 Statistics kernel

- fixed golden fixtures checked against an independent reference calculation;
- exact SplitMix64 constants/state transitions, rejection threshold, 53-bit
  restart uniforms, strict comparison, little-endian index bytes, prefix hash,
  and result for a fixed seed;
- null family, known positive edge, and all-negative family;
- serial correlation and cross-candidate correlation with shared indices;
- many poor alternatives to prove SPA is less distorted than unstudentized RC;
- candidate-column permutation invariance after canonical sorting;
- zero/negative long-run variance, constant series, NaN/Inf, ragged input,
  misaligned periods, short samples, invalid block length, and `K < 2`;
- inclusive exceedance and plus-one p-values, including the no-exceedance case;
- lower/consistent/upper recentering thresholds;
- the exact conditional variance expression, conservative
  `sqrt(B)/(B+1)` ambiguity radius, and preregistered replication-schedule
  behavior;
- exact bootstrap-prefix reuse and rejection of reset/discarded streams; and
- deterministic block-grid construction, any-grid method-disagreement priority,
  maximum-p decision, and sensitivity fixtures without post-result parameter
  selection; and
- canonical JSON fixtures for NFC/non-NFC Unicode, duplicate keys, UTF-8,
  negative-zero normalization, float-hex, CRLF rejection, and final LF.

The independent reference fixture may use a clearly isolated test-only
implementation. Production does not import `arch` or any network-capable
package.

### 8.2 Authority and artifacts

- clean schema-v2 to v3 migration with no invented historical evidence, plus
  regression proof that every old search/evaluation write/read/recovery and
  portable-capture path and the complete pre-Phase-B lifecycle behavior still
  work on v3; Phase A exposes no v4 verifier or lifecycle effect;
- corrupt or missing v3 family tables fail the exact family verifier even when
  the live trial-ledger v2-subset check and portable capture remain readable;
- the authority, not callers, issues scope/program/decision/family/exposure IDs;
  a second publishable decision in one selection scope, exact exposure-ID reuse, a
  fresh ID with the same fingerprint, overlapping sibling exposure contracts,
  stale sibling-history heads, and cross-family CAS attempts all fail closed;
- two legitimate but different research questions, strategy roots, or programs
  that target the same deployment mandate resolve to the same selection scope;
  attempting to obtain a second publishable slot through any of them fails;
- new-owner/free-text/process-ID/question/strategy-root changes cannot alter a
  deployment selection scope; scope or question creation without the audited
  multi-party grant, alias remapping, re-rooting a registered
  source/descendant/config, or passing caller-selected scope/question/root IDs
  all fail closed, and every authority-event chain is tamper checked;
- declaration must precede search, and the family OOS handle must remain
  unavailable until both the verified freeze event and committed whole-family
  access attempt;
- every raw proposal, including syntax-invalid/manual/failed/duplicate
  proposals, must be atomically registered before parsing, scoring, data, or
  evaluator access; malformed/non-UTF-8 envelopes retain their exact opaque
  byte hash while only successfully parsed proposals receive a separate
  canonical hash; proposal/config/alpha identities and duplicate mappings are
  deterministic;
- raw envelopes containing Bearer/API-key patterns, PEM/private-key material,
  credential-shaped fields, restricted-data signatures, or policy-defined
  high-entropy strings, plus scanner-unavailable/error cases, write no raw byte
  to evidence/Git/ordinary storage/logs/backups, never reach a parser, and make
  the family `NOT_ASSESSABLE`; clean invalid bytes remain restricted and public
  artifacts contain safe metadata only. Tests recursively traverse the closure,
  ArtifactCore, authority projections, envelope, and publication sidecar and
  prove that every quarantined row exposes no raw hash, result-derived hash,
  exact length, restricted blob reference, or restricted authority-chain head;
- `raw_proposal_count`, `unique_performance_hypothesis_count`,
  `global_unique_config_count`, and `performance_access_attempt_count` are
  independently recomputed; invalid/byte-identical duplicate proposals do not
  inflate the existing DISTINCT-config DSR count;
- an adaptive generator cannot observe any family-OOS result before it stops;
  deliberately releasing OOS early or omitting an evaluated proposal
  invalidates closure;
- every terminal search must have a matching immutable emission seal;
- all linked searches must be terminal before closure;
- every proposal must have exactly one valid disposition; evaluation-started
  exception/timeout/missing-series cases must become
  `PERFORMANCE_ATTEMPTED_INCOMPLETE` for every accepted proposal and duplicate
  in the same finalization/recovery transaction and force `NOT_ASSESSABLE`;
  every DLP-denied/error proposal must have exactly one `SECRET_QUARANTINED`
  disposition, no canonical/config/evaluation link, and the same forced family
  result;
- whole-family atomic-batch crash fixtures prove all existing-authority
  evaluation payloads/audit-outbox intents, dedicated family-OOS links, sealed
  source-search/config references, proposal dispositions, benchmark, strict
  period keys, gross/cost/net/turnover series, and terminal attempt record
  commit together or not at all;
- kill-point tests cover before access-attempt commit, after committed `STARTED`
  but before byte release, after first byte, between candidate evaluations,
  after computation, after every locked batch insert, after batch commit, and
  process restart. No byte is visible before `STARTED`; every post-`STARTED`
  pre-commit kill remains permanently consumed and becomes durable
  `INCOMPLETE` without retry; only the fully committed batch is `COMPLETED`;
- transaction-trace tests prove public methods own one outer
  `BEGIN IMMEDIATE`, locked insert/transition primitives never issue transaction
  control or open another connection, nested `BEGIN` is impossible, and every
  injected failure rolls back the composite write;
- exact `gross - cost = net`, dynamic cost-input hash, non-empty unique strictly
  increasing UTC period keys, and `event_time=None` rejection;
- missing series, period mismatch, dataset/cost/benchmark/code drift, new linked
  trial, and stale trial-ledger snapshot all fail closed; a descendant with
  identical governed blobs remains eligible, while one changed load-bearing
  blob preserves historical verification but blocks promotion;
- `CODE_MANIFEST_POLICY_V1` regenerates the exact repo-local transitive and
  dynamic-registry/config path set; fixtures that omit an imported helper,
  dynamically registered operator/evaluator, policy/config, or event schema,
  or that supply a caller path list, are rejected before blob comparison;
- OOS release requires a declaration-identical `ExecutionCodeAttestation` and
  immutable-artifact lease; changing a helper after declaration then restoring
  it, changing a runtime lock, using a mutable worktree, losing the lease, or
  returning different start/end attestations permanently yields INCOMPLETE with
  no committed return receipts;
- missing, altered, duplicated, or calendar-misaligned benchmark return series
  fail closed;
- append-only update/delete trigger enforcement for every new table;
- row, manifest, artifact, and content-address filename tampering detection;
- deterministic `ArtifactCore` and `PublishedArtifactEnvelope` bytes from
  identical authoritative rows; exact-core and exact-envelope DLP scans plus a
  separately hashed fixed-schema publication-receipt projection have no
  self-reference; clean-checkout tests recompute its complete genesis-to-row
  authority chain and reject detached/truncated/foreign-lineage sidecars, while
  a missing/mismatched receipt or either scan denial prevents publication;
- `REAL_GOVERNED_FAMILY_V1` accepts only the configured governed live authority
  or its exact official offline authority projection, together with observed
  PIT catalog lineage, and rejects fixture/demo/mock/manual-import authorities,
  datasets, return matrices, detached payloads, and test-only replication runs;
- a later portable-ledger seal cannot erase or invalidate a content-addressed
  older family proof, while a new member of the same family is rejected; and
- an honest non-authority `NOT_ASSESSABLE` historical audit artifact for the
  current incomplete 39/8 material, with no family rows or EvidenceRef.

### 8.3 Phase B promotion boundary

- exact v3-to-v4 activation requires the exclusive writer lease, stops all old
  writers, atomically adds state versions/tables/triggers and inserts the
  complete immutable legacy-state baseline and activation marker last; kill
  injection leaves exact v3 or complete v4, never a mixed schema, while a stale
  v3 writer and every unversioned post-activation state mutation fail closed;
- no candidate advances during incomplete multi-round or multi-search families;
- only the deterministic manifest winner can consume evidence;
- a pre-cutover TESTING winner can proceed only through the unique
  `LEGACY_TESTING_ADOPTED_FOR_FAMILY` no-state-change versioned record bound to
  the activation snapshot and complete new family; pre-cutover
  VALIDATED/PAPER/APPROVED/REJECTED/RETIRED and post-cutover unrelated TESTING
  winners cannot adopt or promote, while duplicate non-winners never change;
  fixtures with pre-existing APPROVED and REJECTED non-winners prove that no
  family operation demotes, overwrites, pins, or otherwise mutates them;
- the unique `DRAFT -> TESTING` seam rederives the aggregate result from every
  assessment-grid row and rejects `DOES_NOT_SUPPORT_SUPERIORITY`,
  `METHOD_DISAGREEMENT`, `PENDING_MORE_REPLICATIONS`,
  `MONTE_CARLO_UNRESOLVED`, `NOT_ASSESSABLE`, any ambiguous/non-rejecting grid,
  and every replication/policy-ineligible result; the `TESTING -> VALIDATED`
  seam repeats the same checks rather than trusting the earlier transition;
- direct booleans, caller-built result objects, mismatched refs, stale refs, and
  manual method selection are rejected;
- `ResearchAgentLoop` and every intelligence path are mechanically forbidden
  from positive direct lifecycle transitions around
  `DeferredFamilyPromotionService`;
- versioned `alpha_state`, append-only evidence pin, hash-chained
  `alpha_transition_records`, outbox event, and `WINNER_PINNED` selection-scope outcome
  commit in one ExperimentStore CAS transaction; injected kills at every write
  leave either all five authorities or none;
- `CLOSED_WITHOUT_PROMOTION` is created only by its unique append-only CAS seam;
  a second outcome, conversion to a winner, an unpinned old ref after the exact
  selection-scope tip advances, or sibling OOS release before either outcome all fail
  closed; any pre-existing winner state may close without alpha mutation when
  this family created no pin/validated-or-later transition, so a duplicate
  config cannot deny service to the sole slot; deterministic adoption or
  `TESTING -> VALIDATED` versus close races produce exactly one winning outcome
  with no partial state;
- every `-> PAPER_TRADING` and final human `-> APPROVED` uses the unique
  transactional positive-transition seam to CAS state/version, reload the same
  pin/scope outcome/family/winner/grant, and atomically append transition/outbox;
  concurrent maintenance or promotion and injected kills leave no partial
  authority; a newer unrelated evaluation cannot replace the winner, and legacy
  advanced states without a pin fail closed;
- both positive-transition seams recompute current DISTINCT-config DSR and BHY
  population/survivor state under the ExperimentStore lock, verify stable
  immutable heads for every other guard authority, and persist the exact
  `PromotionGuardSnapshot`; tests insert a new trial or change the FDR population
  after an outer precheck and prove the transaction rejects/recomputes rather
  than consuming the stale result;
- final human approval consumes the exact ApprovalStore request before the
  ExperimentStore transition; two-worker races and kills before consume, after
  consume, before state commit, and after state commit prove at-most-once grant
  use, burn-on-failure, no compensation/reuse, and recovery by the unique
  transition/consumption binding;
- legacy catalog snapshot/put APIs cannot change existing state/version, and
  governance tests reject every direct SQL/API lifecycle bypass;
- RC/SPA pass cannot bypass any existing evaluation/risk/actor gate;
- disagreement, Monte Carlo ambiguity, missing data, and tampering park the
  family without promotion; and
- all L2 safety flags and the non-human `PAPER_TRADING` ceiling remain enforced.

The top-tier assessment test is semantic, not `paths_exist`: it loads the
content-addressed payload, verifies 100% governed-family coverage, recomputes
canonical hashes and RC/SPA results, validates the Phase-B chokepoints, and
requires one `REAL_GOVERNED_FAMILY_V1` `ASSESSED` family from the configured
live project authority or exact official offline projection. Either a supporting or
non-supporting statistical verdict may prove that the control works; an
undeclared historical audit, `NOT_ASSESSABLE` family, kernel-only test fixture,
demo/manual-import family, detached artifact, or mere evidence filename cannot
raise the capability score.

## 9. Rejected alternatives

### 9.1 Run RC/SPA on aggregate Sharpe rows

Rejected. Block bootstrap requires a common time-series matrix. Aggregate
metrics cannot reconstruct serial or cross-candidate dependence.

### 9.2 Use only the eight available evaluations

Rejected. Dropping 31 registered candidates after the search is precisely the
selection-history omission the control is intended to prevent.

### 9.3 Pool searches with different datasets or costs

Rejected. The differential columns would not represent one comparable family
or one selection decision.

### 9.4 Promote per candidate and attach family evidence afterward

Rejected. Evidence calculated after a lifecycle transition is an audit note,
not a load-bearing gate.

### 9.5 Pass a caller-computed boolean into the evaluator

Rejected. A boolean loses the family manifest, algorithm parameters, selected
winner, staleness checks, and tamper evidence.

### 9.6 Add `arch` as a production dependency

Rejected. QUANTOS needs a small, auditable, versioned kernel with stable random
indices and canonical evidence. The `arch` implementation remains a useful
independent reference, not a runtime dependency.

### 9.7 Continue adaptive search while observing the family OOS

Rejected. Registering a candidate before its own evaluation does not close
adaptive leakage when earlier OOS results generate later candidates. The
generator must stop and the family must freeze before the common OOS is
released. Replaying the complete adaptive search inside every bootstrap
replication is a different, much more expensive design and is outside scope.

### 9.8 Reconstruct return series from mutable catalog state

Rejected. `AlphaCatalog.set_returns` is mutable, does not bind period keys or
gross/cost decomposition, and is not atomic with the immutable evaluation/search
link. Only the OOS evaluation receipt transaction is authoritative.

### 9.9 Bind a family only to the replaceable latest ledger files

Rejected. A later seal can replace the fixed snapshot/history files and make an
old family proof impossible to reproduce. Publishable family evidence retains a
content-addressed exact ledger proof.

### 9.10 Mark capability complete because evidence paths exist

Rejected. The top-tier checker must semantically verify and recompute a real,
complete governed-family assessment plus its promotion chokepoints. File
existence, kernel unit tests, or an honest `NOT_ASSESSABLE` audit is not proof of
the deployed control.

## 10. Safety and non-goals

- No broker SDK, order submission, credential access, network call, or live
  trading path is added.
- `LIVE_TRADING_ENABLED=false`, `REAL_BROKER_ENABLED=false`,
  `ALLOW_ORDER_SUBMISSION=false`, and `ALLOW_AI_DIRECT_TRADING=false` remain
  unchanged; human approval remains required.
- RC/SPA does not replace Deflated Sharpe, BH-FDR, PBO/CPCV, purge/embargo,
  leakage checks, cost/capacity realism, paper tracking, or natural-forward
  evidence.
- Phase A does not change lifecycle behavior. Phase B cannot raise a non-human
  actor above `PAPER_TRADING`.
- No existing evidence is rewritten to manufacture a pass.
- No Git stage, commit, push, external backup, Docker build, or external service
  mutation is authorized by this design.

## 11. Acceptance criteria

Phase A is accepted only when:

- the deterministic kernel matches independent golden fixtures and all invalid
  inputs fail closed;
- a publishable run requires an authority-issued deployment selection scope,
  complete program/question/lineage-root set, decision/exposure IDs, the
  scope-level sole-slot reservation, a declared/frozen family, verified scope
  history, one durably committed and
  permanently consumed blind common-OOS attempt, one complete atomic
  whole-family return batch, complete proposal dispositions, at least 10,000
  replications per preregistered block-grid point, and immutable authority
  hashes;
- opaque invalid-envelope identity and parsed canonical identity remain
  distinct, all four counters reconcile, and duplicate configs do not inflate
  the existing DISTINCT-config DSR multiplicity; DLP-denied/error envelopes
  receive exactly one safe-metadata-only `SECRET_QUARANTINED` disposition,
  reach no parser/evaluator/public raw-byte sink, and force
  `NOT_ASSESSABLE`; no closure/core/projection/envelope/sidecar field exposes a
  quarantined raw hash, result-derived commitment, exact length, restricted
  blob reference, or restricted chain head;
- ExperimentStore v3 migration, v2-subset forward compatibility, every old
  search path, pre-extracted locked primitives, no-nested-transaction tests,
  one-shot kill-point tests, and all new append-only/tamper checks pass, while
  the old lifecycle behavior remains unchanged and no v4 activation/verifier is
  available in Phase A;
- deployment-scope/mandate aliases and research-question/lineage issuance are
  authority-owned, grant-audited, append-only, and cannot be reset through
  caller IDs, owner/text/question/root changes, descendant aliases, config
  re-rooting, or splitting one mandate across multiple programs;
- identical pinned-runtime inputs reproduce the same index-stream hash,
  statistics, p-values, and content-addressed artifact;
- the authority-owned code-manifest policy regenerates an exact complete
  transitive/dynamic/config path set at declaration and verification time;
  omitted helpers or registry modules fail closed and callers cannot choose the
  path list;
- OOS evaluation runs only under a declaration-identical immutable-artifact
  `ExecutionCodeAttestation` and unbroken lease, and every batch/return receipt
  binds the identical start/end execution identity;
- the acyclic core/envelope publication format passes exact-byte DLP scans,
  publishes no opaque raw envelope, and is accepted only with its independently
  hashed fixed-schema publication-receipt projection whose complete
  genesis-to-row authority chain, lineage, policy, terminal head, and exact
  envelope binding recompute;
- the current 39/8 material is reported only as a non-authority historical
  `NOT_ASSESSABLE` audit scope;
- no alpha state, evaluator verdict, or top-tier claim changes; and
- the canonical governance scanner remains clean.

Phase B is accepted only when:

- one exclusive v3-to-v4 activation transaction adds state versions,
  pin/transition authority, triggers, the immutable per-alpha legacy baseline,
  and the activation marker last; failures leave exact v3, while
  post-activation stale writers and all unversioned state writes fail closed;
- promotion is deferred until the complete family is closed and assessed;
- only the deterministic winner can use a verified, current assessment through
  the unique deferred-promotion service and lifecycle authority verifier;
- both the `DRAFT -> TESTING` and `TESTING -> VALIDATED` transactions rederive
  and require exact `ASSESSED` plus `SUPPORTS_SUPERIORITY`, every-grid
  uncertainty-resolved RC/SPA rejection, and eligible replication/policy state;
  every other diagnostic verdict and grid state is proven non-promotable by
  negative tests;
- validation atomically commits versioned `alpha_state`, the append-only alpha
  evidence pin, hash-chained `alpha_transition_records`, outbox event, and the
  exact `WINNER_PINNED` selection-scope outcome; kill injection leaves all five or none;
- an unchanged pre-cutover TESTING winner has one explicit, baseline-bound
  adoption seam before validation, with no fabricated DRAFT history; every
  other pre-existing non-DRAFT state is non-promotable;
- `CLOSED_WITHOUT_PROMOTION` has one irreversible audited CAS seam, accepts any
  observed pre-existing winner state without mutating it when this family has
  no pin/validated-or-later transition, permanently invalidates that family ref
  for promotion, and wins or loses atomically against concurrent
  adoption/validation CAS; sibling OOS release is impossible before either
  outcome;
- every later paper/final-human transition atomically CAS-updates state/version,
  reloads the same pin/scope outcome/exact winner/grant, and appends its
  transition/outbox; a historical unpinned ref cannot be consumed after the
  selection-scope tip advances, while an already locked winner permits only
  verified diagnostic-only suffixes; concurrent or injected failures leave no
  partial authority, legacy snapshot APIs cannot mutate state/version, and
  advanced states without a persisted pin/transition record fail closed;
- each positive transition recomputes current trial-count/DSR and BHY population
  under the store lock, verifies stable immutable heads for all remaining guard
  authorities, and binds the resulting `PromotionGuardSnapshot`; new-trial/FDR
  population races cannot consume a stale outer guard decision;
- final human approval remains burn-first and at-most-once: only an exact
  consumed request/head can enter the state transaction, its consumption hash
  is unique in transition authority, post-consume failure remains burned, and
  no worker or recovery path can reuse it;
- significance requires both White RC and SPA-consistent results, outside the
  conservative Monte Carlo ambiguity radius at every grid point, with any-grid
  method disagreement evaluated before maximum-p summaries;
- every missing, stale, tampered, incompatible, or disagreeing case is
  non-promotable; and
- the semantic top-tier checker recomputes at least one
  `REAL_GOVERNED_FAMILY_V1` `ASSESSED` family from the configured live project
  authority or its exact official offline projection, rejects
  fixture/demo/manual/detached evidence, verifies the actual-execution
  attestation/lease and publication-scan receipt, and verifies the load-bearing
  chokepoints before crediting the capability; and
- the full canonical validation gate is rerun, with any remaining failures
  limited to independently missing or stale external evidence already
  documented outside this feature.

## 12. References

- Halbert White, "A Reality Check for Data Snooping," *Econometrica* 68(5),
  2000: <https://onlinelibrary.wiley.com/doi/10.1111/1468-0262.00152>
- Peter R. Hansen, "A Test for Superior Predictive Ability," *Journal of
  Business & Economic Statistics* 23(4), 2005:
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=264569>
- `arch` 7.2.0 SPA documentation and source, used only as an implementation
  cross-reference:
  <https://arch.readthedocs.io/en/latest/multiple-comparison/generated/arch.bootstrap.SPA.html>
  and
  <https://arch.readthedocs.io/en/latest/_modules/arch/bootstrap/multiple_comparison.html>
