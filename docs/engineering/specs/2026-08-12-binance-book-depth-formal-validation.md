# Binance book-depth formal validation contract

Status: **frozen before confirmation outcome access**  
Readiness: **L2 historical validation only**

## Decision

The one-shot confirmation remains a single frozen subject evaluation.  A PASS
does not itself admit the Alpha.  Before that result is observed, this contract
freezes the production validation DAG inputs, thresholds, population accounting
and one comparable control.

The comparable series is the already-registered positive-direction version of
the exact same 1% depth-imbalance hypothesis.  It is a negative-direction
control for DSR, FDR and PBO only.  It cannot replace the subject, become an
admission winner, or create another selection after the confirmation result is
known.  Its return series is derived exactly by negating the subject gross
return and applying the identical frozen turnover cost; this uses the same
calendar without reading or scoring another candidate from the recovered
market authority.

## Conservative trial bridge

The recovered experiment registry contains 2,823 immutable configuration
hashes.  The validation DAG uses canonical definition-envelope hashes, which
are a different identity domain.  The bridge does not claim that the two hash
forms are equal and does not overwrite registry history.  It counts every
registry trial plus the subject and control validation envelopes, for a frozen
effective count of 2,825.  Trials without comparable return histories remain
independent and receive no correlation discount.

This is deliberately stricter than counting only the two aligned series.  If
catalog-wide DSR cannot pass with honest available evidence, the outcome is a
BLOCK; a later experiment would need a new pre-registered population and new
untouched data, not a post-outcome threshold or control change.

## Fixed gates

The DAG order is `cheap -> oos -> realism`, with fail-closed short circuiting.
OOS uses the exact 84 non-overlapping 24-hour decisions, sequential 36/12/12
walk-forward splits, six purged folds with one-period embargo, BH/BHY plus
family FDR at q=0.10, DSR >= 0.95, PBO <= 0.50, and non-negative subject means
in every computed OOS fold.

Realism retains the frozen six-basis-point one-way cost and separately applies
an uncalibrated-principled 5--20 impact-coefficient band.  Capacity must be at
least USD 100,000, cost fraction no more than 6 bps, CVaR no more than 3%, Hill
tail index at least 2, profitable-regime fraction at least 0.5, and lower-tail
dependence no more than 0.5.  ADV comes only from checksum-verified public
Binance USD-M 1-minute kline quote volume covering the exact confirmation days.

## Safety and failure semantics

The contract grants no forward credit, Testnet authority, credential access,
broker connectivity, order submission, Mainnet, Micro-Live or real-capital
authority.  Missing data, timeout, 429, 404, checksum mismatch, empty output,
malformed evidence, or any stage BLOCK remains a BLOCK.  Even a production DAG
PASS requires a separate forward-paper admission before any Testnet intent can
exist.
