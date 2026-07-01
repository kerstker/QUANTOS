from services.research.macro_regime import bucket_returns, sign_regime, tercile_labels


def test_tercile_labels_splits_three():
    labels = tercile_labels([1, 2, 3, 4, 5, 6, 7, 8, 9])
    assert labels[0] == "low" and labels[-1] == "high" and "mid" in labels


def test_tercile_preserves_none():
    labels = tercile_labels([1, 2, None, 8, 9])
    assert labels[2] is None


def test_sign_regime():
    assert sign_regime(-0.5) == "inverted"
    assert sign_regime(0.3) == "normal"
    assert sign_regime(None) is None


def test_bucket_returns_groups_and_scores():
    rs = [0.02, -0.01, 0.03, -0.02]
    lb = ["high", "low", "high", "low"]
    out = bucket_returns(rs, lb)
    assert out["high"]["n"] == 2 and out["low"]["n"] == 2
    assert out["high"]["mean_return"] > out["low"]["mean_return"]


def test_bucket_returns_ignores_none_labels():
    out = bucket_returns([0.01, 0.02], ["a", None])
    assert "a" in out and None not in out and out["a"]["n"] == 1
