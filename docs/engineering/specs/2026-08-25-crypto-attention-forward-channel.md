# Crypto attention forward channel

Status: IMPLEMENTED, VALIDATED, AND COLLECTING FORWARD VINTAGES ON DGX

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

At verification time the forward database contained 52 rows: 13 instruments,
one distinct event day (`1787443200000`), and four distinct fetch-time
knowledge vintages (`1787617550963` through `1787620110457`). This is still one
forward observation date, not four independent forward days. The snapshot
SHA-256 is
`7d0557a3614f9eb759b9406d27bcb091f725ac619a7cc7eab86950b33aa64702`;
the bitemporal database SHA-256 is
`fe07cd4e74d4d4d13056bec55327f11929219823dfcb320a3c79596e07abe725`.
The methodology audit therefore remains honestly
`FORWARD_PREREG_REQUIRED`, with `alpha_eligible_count=0`.
