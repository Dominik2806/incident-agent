# 0008: A demo app with an intentional bug that is never fixed on main

- **Status:** Accepted (PR #10, client correlation in PR #11)

## Context

An agent can name a cause from logs, but a useful fix proposal needs the real code. The stack traces in the sample
logs should lead to files and lines that exist.

## Decision

- `demo_app/` is a tiny, stdlib-only checkout and payment service. Payment v1.4.2 reads `payload["currency"]`
  without a default, which is the `KeyError: 'currency'` of the `checkout-500` scenario. Legacy clients do not send
  the field.
- The stack frames of `payment` and `checkout` in the generator come from one table (`data/frames.py`) and point to
  real lines of `demo_app/`. A test checks the alignment; the frames of `catalog` and `auth` stay synthetic.
- The bug is never fixed on `main`. `tests/test_demo_app.py` has a strict `xfail` (`raises=AssertionError`): fixing
  the bug turns it into a failure, so the marker can only be removed on purpose.
- `demo_app/` is not part of the wheel; pytest finds it through `pythonpath = ["."]`.

## Consequences

- `fix-proposer` can read the failing line, and a correct patch would turn the `xfail` test green.
- Moving a line in `demo_app/` requires updating `frames.py`, otherwise a test fails.
- The repository contains a known bug on purpose; this is documented in the README and in
  [demo-app.md](../demo-app.md) so that nobody fixes it by accident.
