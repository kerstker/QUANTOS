# Crypto attention forward channel

Status: IMPLEMENTED, VALIDATION AND DGX FIRST CAPTURE PENDING

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
