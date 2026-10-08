# Crypto attention forward channel

Status: IMPLEMENTED, VALIDATED, COLLECTING, AND FORWARD PROTOCOL FROZEN

## Purpose

The next independent Alpha branch is attention-conditioned momentum. Liu and
Tsyvinski report direct crypto evidence for time-series momentum
and investor-attention predictability, but their result is an economic prior,
not a deployable strategy. QUANTOS must first obtain an honest timestamped
attention channel before it defines or evaluates a trading rule.

Primary research source:

- Yukun Liu and Aleh Tsyvinski, *Risks and Returns of
  Cryptocurrency*, Review of Financial Studies 34(6), 2021;
  DOI `10.1093/rfs/hhaa113`; working-paper record `10.3386/w24877`.

Operational data source:

- Wikimedia Analytics API, daily per-article pageviews, English Wikipedia,
  `all-access`, agent=`user`. The user filter removes traffic classified by
  Wikimedia as spiders or automated agents. Redirect aliases are not used
  because redirect requests are not attributed to the canonical target page.

## Fixed channel contract

`tools/wikimedia_attention_fetch.py` collects 13 pinned canonical pages:
ADA, AVAX, BCH, BTC, DOGE, DOT, ETC, ETH, LINK, LTC, SOL, TRX, and XRP. The
mapping is code-reviewed and must not change after inspecting returns.

Every refresh has two deliberately separate outputs:

1. A 120-day retrospective snapshot for data-quality and association
   diagnostics. It is marked `RETROSPECTIVE_API_NO_CAPTURED_VINTAGES`,
   `pit_admissible_for_promotion=false`, and `promotion_eligible=false`.
2. One latest common completed-day cross-section in
   `crypto_attention_vintages.db`. Its event time is the pageview day and its
   knowledge time is the actual fetch time. The 13 rows commit atomically; an
   incomplete common day fails closed.

The scheduled wrapper runs daily at 09:45 Asia/Shanghai through the immutable
L2 schedule entrypoint. It uses no credentials, account endpoints, broker SDK,
or order surface. The collector itself cannot create an Alpha, evaluate
returns, enter Shadow/Testnet, or grant live authority.

The public endpoint is rate limited. The reviewed collector spaces article
requests by 250 ms and retries HTTP 429/500/502/503/504 responses. It honors a
numeric `Retry-After` header, otherwise uses bounded exponential backoff, and
caps any single wait at 60 seconds. A non-transient response such as 404 fails
immediately. Exhausted retries fail the whole refresh before any partial
forward cross-section is committed.

## Research admission boundary

Historical pageviews cannot support promotion because QUANTOS did not capture
their contemporaneous revisions. The methodology audit must therefore report
`FORWARD_PREREG_REQUIRED` after the channel appears, never
`HISTORICAL_DIAGNOSTIC_READY`.

A separate, future commit must freeze the exact attention innovation,
price-momentum window, label horizon, costs, controls, minimum forward sample,
and rejection bars before any return outcome is accessed. Required controls at
minimum are the price-only rule, a deterministic timestamp placebo, source
outage handling, and turnover/crowding pressure. No parameter scan is allowed
on the forward sample.

## DGX deployment evidence

The rate-limit-hardened release is the complete Git checkout at commit
`af41c762f82d14cd6ccebf00ec9408df21a2ea5f`. Its transfer bundle SHA-256 is
`7b636a87633597c705dab3038495eff796fba42b527fd78660c729c3bb42a1f8`.
The local canonical validation gate passed all 27 selected stages with zero
failures or timeouts. The immutable DGX checkout then passed 187 focused tests
covering the fetcher, forward worker, scheduler entrypoint, watchdog, channel
registry, and systemd/data governance boundaries.

The final immutable entrypoint and the installed systemd service each completed
successfully with 13 instruments, 119 retrospective common days, and 13 new
forward-vintage rows. The timer is enabled and waiting for its daily 09:45
Asia/Shanghai trigger. Its unit binds the exact release commit above and keeps
all five non-negotiable live-safety flags literal and fail-closed.

At final verification time the forward database contained 65 rows: 13
instruments, one distinct event day (`1787443200000`), and five distinct
fetch-time knowledge vintages (`1787617550963` through `1787622302559`). This
is still one forward observation date, not five independent forward days. The
fifth vintage came from the first unattended 09:45 systemd trigger, which used
the pinned `af41c762f82d14cd6ccebf00ec9408df21a2ea5f` collector release and
exited successfully. The final snapshot SHA-256 is
`fb60d47f7cb787c08d5637ae70cbc0be1ff30fdf862a94da813688b1555e9261`;
the bitemporal database SHA-256 is
`c9a6d0061fa87c62f3b6a6836afe3896c4c98f1600c0273b72061cfa57054083`.
The methodology audit therefore remains honestly
`FORWARD_PREREG_REQUIRED`, with `alpha_eligible_count=0`.

## Frozen forward experiment

`services/research/attention_momentum.py` contains the deterministic pure
signal primitives. `tools/attention_momentum_preregistration.py` contains the
append-once experiment contract and reads no attention values, prices, labels,
returns, registry, lifecycle, broker, credential, or order surface.

The frozen primary signal is a 7-day cross-sectional price-momentum rank
conditioned by the positive part of a 28-event-day Wikimedia attention
innovation rank. It trades the first complete Binance USDM daily close strictly
after the attention vintage's actual knowledge time and exits at the next
complete daily close. The 13-asset universe, top/bottom 25% construction,
$1 million diagnostic AUM, 5% participation cap, costs, double-cost pressure,
and four-candidate family are fixed with no parameter scan. The three controls
are price-only momentum, a prior-only deterministic timestamp placebo, and
attention-only.

Only the earliest complete 13-instrument vintage per event day whose knowledge
time is strictly after the DGX preregistration timestamp is admissible. Missing
attention days are not forward-filled or dropped: the frozen book is cash for
that outcome day and the outage is counted. Missing execution or exit prices
fail closed as data-integrity rejections.

Return statistics remain forbidden until both conditions hold: UTC
`2027-08-31T00:00:00Z` has passed and at least 252 eligible independent
one-day outcomes exist after the 28-day attention warm-up. Before then, only
data-health counts and hashes may be inspected. The future one-shot evaluator
must register exactly four candidates in the global trial ledger before any
outcome access and must apply DSR, PBO, cost, drawdown, participation,
incremental-vs-price-only, timestamp-placebo, crowding/capacity, regime, and
tail-risk bars. Even a terminal pass can authorize only a human-reviewed L2
paper admission; it grants no Testnet or live authority.

The protocol release is
`f358411c763f85a87b97b022814bdfb09db8c946`; its complete-history bundle
SHA-256 is
`49ed249dd22f6cd1b624c8c81557a884903c60138c4e90ae86fee032236fa938`.
The canonical gate passed all 27 stages after 6,593 core tests, 2,821
live/trading/tools tests, 199 frontend tests, and 24 final-audit tests. The DGX
ARM64 release passed the 12 new protocol tests.

DGX recorded the append-once contract at `1787623101762` with contract hash
`dd5fdc45b744adb76caf3a64f5966e4cc853e335a4aa93d62b91d303026b848b`
and preregistration ID
`a5697ed13d555817a2652fb947b8e4511571b960bca2f382c87cec26f5b08788`.
The artifact SHA-256 is
`db38bbc95eba3d49fd26aaebbae5b8d716d8aa162c3c4e35a31a44c2020ce4e0`;
it is owned by `root:quantos` with mode `0440`. A deliberate second recording
failed with `FileExistsError` and left the hash unchanged. Because the
preregistration timestamp is later than the current maximum captured knowledge
time (`1787622302559`), none of the already observed vintages is admissible to
the terminal experiment.
