# Demo app

`demo_app/` is a tiny checkout/payment service that contains the bug behind the `checkout-500` scenario. It exists so
that the agent can find the failing code from a stack trace and propose a real fix.

**The bug is intentional and is never fixed on `main`.** It is the basis of the demo.

## The story

Payment v1.4.2 reads `currency = payload["currency"]` without a default. Older clients do not send `currency`, so
those requests raise `KeyError: 'currency'` in `process_charge`. Checkout catches the exception and relays it to the
customer as HTTP 500 with the message `payment service error: KeyError: 'currency'`. This is exactly what the
generated logs show.

## File map

| File | Content |
|---|---|
| `demo_app/payment/__init__.py` | `__version__ = "1.4.2"` |
| `demo_app/payment/charge.py` | `process_charge`: **the bug** (`payload["currency"]`), then the gateway call |
| `demo_app/payment/gateway.py` | `authorize`: simulated card gateway, can raise `TimeoutError` |
| `demo_app/payment/server.py` | `handle_charge`: calls `process_charge`, lets errors propagate |
| `demo_app/checkout/server.py` | `handle_checkout`: calls payment, relays any failure as HTTP 500 |

Standard library only: no web framework, no server, no extra dependency. It is not part of the wheel (`uv_build`
packages only `src/incident_agent`); `pytest` finds it through `pythonpath = ["."]` in `pyproject.toml`.

## Generator and line numbers

The stack frames of the `payment` and `checkout` services in the generated logs point to real files and lines of this
app. They are defined once, in `src/incident_agent/data/frames.py`. **The generator and `tests/test_demo_app.py`
depend on these line numbers**, so every mapped file carries a comment saying so. If you move a line, update
`frames.py`; the alignment test fails until you do.

Frames of `catalog` and `auth` stay synthetic (`/app/...` paths): the demo app has no code for those services and the
incident does not touch them. The `ConnectionResetError` one-off in `checkout` is also simulated: its frames are real
code, but an in-process call cannot raise a socket error.

## Tests (`tests/test_demo_app.py`)

- Checkout with `currency` returns 200.
- A gateway `TimeoutError` is relayed as HTTP 500.
- **Strict xfail** reproduces the bug: a payload without `currency` must not end in an unhandled `KeyError` or HTTP 500
  (a default currency or a 4xx validation error both pass). It is `strict=True` so that fixing the bug turns the
  unexpected pass into a failure; whoever fixes it must remove the marker on purpose and cannot do it by accident.
  It uses `raises=AssertionError` so that an `ImportError` or a typo does not count as the expected failure.
- Frame alignment: every payment/checkout frame in the generated DB points to an existing file and line that contains
  the expected code, and the `KeyError` frames match the traceback the real code produces.

## Reproduce

```bash
uv run pytest tests/test_demo_app.py -v
uv run python -c "from demo_app.checkout.server import handle_checkout as h; print(h({'card': 'tok', 'amount': 1}))"
# Response(status=500, body="payment service error: KeyError: 'currency'")
```

## Using it with the agent

After `uv run generate-data --scenario checkout-500`, report the incident to Claude Code. After `root-cause`, the
`fix-proposer` subagent reads `demo_app/payment/charge.py`, and the report proposes a patch (a diff), a rollback and
the risks. Nothing is applied. A correct patch would make the strict xfail test pass, and the marker would then have to
be removed.

## Example report

`docs/examples/incident-report.md` predates the demo app (it says the code is not in the repo and cites
`/app/payment/charge.py:41`). It will be regenerated.
