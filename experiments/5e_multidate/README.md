# 5E — Multi-date structural calibration

The planned design is 12 chronological first-of-month dates, six fixed windows
per date, and date-level 6/3/3 calibration/validation/test splits. Only the
bounded gates are complete.

Completed:

- deterministic 2025-10-01 00:00–00:10 gate
- all six configured windows for 2025-10-01
- semantic `A_visible` versus `H_hidden` decomposition
- capture-gap/replacement-snapshot recovery outside evaluation windows

Not completed:

- remaining 11 dates
- calibration alpha freeze
- validation dates
- blind final test

Bounded commands:

```bash
python scripts/validate_prompt5e_bounded.py one-window --label run1
python scripts/validate_prompt5e_bounded.py six-window
```

The full command is documented for provenance but must not be run without
explicit approval:

```bash
python -m lobcap.l2.multidate --run-all
```
