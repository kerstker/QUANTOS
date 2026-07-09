"""Top-tier gap audit rank #7: AlphaFactoryConfig and ValidationConfig each independently declare
min_deflated_sharpe/max_pbo (comments say "mirrors ValidationConfig"), but nothing ever checked
that claim — a future tuning pass could edit one default without the other, silently making the
promotion evaluator's gate.evaluate() call (using ValidationConfig's defaults, services/alpha/
evaluator.py:60 `self.gate = gate or ValidationGate()`) and the LATER guard_to_validated transition
check (using AlphaFactoryConfig's defaults, services/alpha/guards.py) disagree — a candidate could
PASS the gate and then be unexpectedly rejected at advance() time, or vice versa. This test makes
that drift fail loudly instead of silently."""
from services.alpha.config import AlphaFactoryConfig
from services.research.validation.gate import ValidationConfig


def test_min_deflated_sharpe_agrees_between_the_two_independently_declared_configs():
    assert AlphaFactoryConfig().min_deflated_sharpe == ValidationConfig().min_deflated_sharpe


def test_max_pbo_agrees_between_the_two_independently_declared_configs():
    assert AlphaFactoryConfig().max_pbo == ValidationConfig().max_pbo
