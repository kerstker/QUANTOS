"""Prospective shared-account allocation convention, not independent alpha books."""

NAMES = ("channel", "reversion", "trend")
POLICY = dict(
    schema="QUANTOS_GOLD_NET_ALLOCATION_POLICY_V1",
    opening="ACTUAL_FILL_PRO_RATA_SAME_SIGN_TARGETS_AFTER_NETTING",
    reduction="ACTUAL_FILL_PRO_RATA_EXISTING_ALLOCATED_INVENTORY",
    inventory="CHANGES_ONLY_ON_ORIGINAL_FILL_OR_LIQUIDATION",
    income="ORIGINAL_ATOMIC_NET_EQUITY_CHANGE_NO_SECOND_CASH_LEDGER",
    funding="ORIGINAL_CASH_FLOW_AND_RECEIPT_AVAILABILITY",
    backstop="UNATTRIBUTED_ACCOUNT_ADJUSTMENT_NOT_ALPHA_INCOME",
    rounding="LEDGER_PRECISION_FIRST_MEMBERS_EXACT_REMAINDER_LAST_SORTED_MEMBER",
    returns="NET_CONTRIBUTION_OVER_PREVIOUS_ACCOUNT_EQUITY",
    independent_strategy_pnl=False,
    economic_authority=False,
    submission_enabled=False,
)
