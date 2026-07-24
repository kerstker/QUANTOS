# GPU research accelerator (L2-safe)

## Scope

QUANTOS now has an optional CUDA path for the isolated research ML layer only.
The backtest, paper-fill, PnL reconciliation, risk gates, audit/event stores,
broker seams, and live-readiness gates remain CPU-only and unchanged.

The default `gbm` and `ridge` stackers still use deterministic scikit-learn.
An explicit `model="xgb", accelerator="cuda"` selects the CUDA-enabled
XGBoost estimator. `accelerator="auto"` may fall back to CPU; explicit CUDA
requests fail closed when the optional backend or device is unavailable.

## Environment

The current workstation exposes one NVIDIA RTX 5090 D device (32,607 MiB), and
the installed XGBoost 3.2.0 build reports CUDA support. The optional dependency
is declared as `research-ml-gpu` in `pyproject.toml`:

```powershell
pip install -e ".[research-ml,research-ml-gpu]"
python -m tools.gpu_research_status
```

The status command is read-only. CuPy is used only to check device visibility;
the XGBoost training path does not require a broker, exchange, network, or
credential. A compatible NVIDIA driver and CUDA-enabled XGBoost wheel are
required on other machines.

## Verification

```powershell
python -m pytest tests/research/test_ml_stacker.py `
  tests/research/test_ml_walk_forward_stack.py `
  tests/research/test_ml_gpu.py -q
python -m tools.ml_stack_research crypto --accelerator=cuda
```

The local run passed 15 ML/GPU tests and completed a real 44,306-row crypto
feature panel with four purged OOS folds. The GPU XGBoost stack had OOS Sharpe
`-0.8019` versus the single-factor baseline `1.7799`; the verdict remains
`REFUTED_NO_EDGE`. GPU availability is therefore a compute capability, not an
Alpha-quality claim, and the snapshot is kept as research evidence only.
