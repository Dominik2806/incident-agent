# 0009: Scenarios never change each other's output; checkout-500 is pinned by a hash

- **Status:** Accepted (PR #13)

## Context

The second scenario (`db-pool`) runs after the shared normal traffic. The first scenario (`checkout-500`) is used by
the demo app, the example report and many tests, and its numbers appear in the docs. Adding scenario code must not
shift a single random draw of `checkout-500`.

## Decision

- A scenario runs last and draws random numbers only from streams derived from its own `rng` (as `db_pool` does).
- Shared noise opt-outs go through `NOISE_SKIP_ONE_OFFS`; skipped entries still consume their random draws.
- Float math uses `+ - * /` only, and only ints and formatted strings reach the database, so output does not depend
  on the platform.
- `tests/test_scenario_db_pool.py` pins the ordered hash of `checkout-500` (seed 42). It is updated only for an
  intended change of that scenario. It was recorded before `db-pool` was added.

## Consequences

- New scenarios are safe to add: an accidental change of `checkout-500` fails a test.
- Authors must follow the draw rules, which are not obvious; they are written down in `CLAUDE.md`.
- An intended change of `checkout-500` needs a deliberate hash update and a docs review.
