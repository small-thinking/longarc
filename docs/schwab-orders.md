# Optional Schwab order capability

The order client is implemented separately from the GET-only review client.
Every `order-*` command defaults to a local plan with **no OAuth or broker
request**. This increment implements and tests the capability; it does not enable
trading, change the Developer Portal App's order limit, or perform a live test.
Scheduled reviews remain read-only. No order response writes the execution ledger.

## Supported operations

| Command | Default | Explicit broker operation |
| --- | --- | --- |
| `order-plan` | Validate/build local JSON only | Always local |
| `order-preview` | Local plan | POST account `previewOrder`; not order submission |
| `order-submit` | Local plan | POST account `orders` |
| `order-status` | Local plan | GET exact account/order ID |
| `order-cancel` | Local plan | GET target, then DELETE exact account/order ID |
| `order-replace` | Local plan | GET target, then PUT exact account/order ID |

Only one CALL leg, LIMIT, NORMAL session and DAY duration are supported. Allowed
instructions are SELL_TO_OPEN and BUY_TO_CLOSE. Quantity must be a positive
integer; price must be a positive decimal string. The optional intent `tick`
accepts `0.01` or `0.05`; it is an explicit input, not broker tick verification.
Market/GTC/conditional/multi-leg orders, BTO and automated roll orders are rejected.
Replacement preserves the contract and side and cannot exceed the observed
remaining quantity. A replacement may create a new broker order ID.

### Local example

Create an **invented** owner-only input, not an executable trade recommendation:

```json
{
  "option_symbol": "QQQ   300118C02000000",
  "instruction": "SELL_TO_OPEN",
  "quantity": 1,
  "price": "5.00",
  "tick": "0.05"
}
```

That symbol is a format fixture; its listing is not asserted. From the repository:

```bash
uv run python -m longarc.cli schwab order-plan \
  --file private/order-intent.json --output private/order-plan.json
uv run python -m longarc.cli schwab order-submit \
  --file private/order-plan.json --output private/submit-dry-run.json
uv run python -m longarc.cli schwab order-cancel \
  --order-id 123456 --output private/cancel-dry-run.json
```

All three commands remain offline. The submit/cancel plan reports the exact
operation confirmation digest; a plan digest is not a submit digest. Digests
bind operation, account index, pinned account hash, target order ID and full
normalized order body. No token, account number or account hash is printed.

## Default-disabled live gate

A broker write requires **all** of these explicit choices:

- Owner-only `private/schwab/trading.json` with `enabled` exactly `true`, an exact
  `account_hash` pin and an explicit positive `max_contracts` limit.
- For BTC, a positive integer `max_buy_debit_u` caps the limit premium debit in
  microdollars; fees are additional, so this is not a total-fee or loss cap.
- For submit/replace, `open_orders_window_verified` exactly `true`. This is an
  **operator readiness attestation**, not automatically established API coverage.
  It must remain false until the provider's date-window/completeness semantics
  are verified for the selected account. This increment does not verify them.
- `--live`, a matching `--confirm-digest` from this exact operation's local plan,
  and a unique `--attempt-id`. Account lookup must match the pinned hash.

No real trading profile is created or enabled by this change. A disabled template:

```json
{
  "enabled": false,
  "account_hash": "COPY_ONLY_FROM_PRIVATE_ACCOUNT_LOOKUP",
  "max_contracts": 1,
  "max_buy_debit_u": 0,
  "open_orders_window_verified": false
}
```

Changing the profile changes the confirmation digest: regenerate and review the
local operation plan. Status GET needs explicit `--live` but does not require
enabling writes; if a profile pin exists, it still must match. Ordinary GET-only
`schwab orders` is available without this optional order workflow.

### Submission preflight

Before submit/replace, fresh broker reads match the selected account to its
positions, exact chain contract, funds, order reservations and regular session.
Source quote time must be within 60 seconds, the chain must disclose no delay or
truncation, and actual deliverables must verify a standard 100-share CALL.

STO reserves shares for existing short calls, pending/part-filled/contingent STOs,
and pending stock sells; an unconfirmed cancellation does not release cover.
BTC cannot exceed existing target shorts after pending BTC reservations and must
have cash for the limit premium. Unresolved derivative identities, missing fields
or malformed quantities stop submission. Replacement releases only the target
reservation verified by exact-ID GET and the current order list.

The order query requests a 190-day window to cover Schwab's documented ordinary
GTC lifetime of up to 180 days. Its response semantics and API range acceptance
are **live-unverified**. Errors, >=3,000 returned rows, or unverified query coverage
block submission; an empty array alone cannot prove account completeness.
Sequential reads can still race with market/account changes. These checks are
execution constraints, not a strategy recommendation or approval.

## Outcomes and recovery

Intent is saved before OAuth or broker traffic. Immediately before the one broker
operation, `request-started.json` is saved. Raw response, preflight evidence and
status readback stay under `private/schwab/orders/attempts/ATTEMPT_ID/`, owner-only.
Reusing an attempt ID is blocked, including after timeout or a process interruption.
There are no automatic broker retries or automatic polling loops.

- HTTP 201 plus a verified account-specific Location means **ACCEPTED**, not filled.
- Cancel HTTP 200/204 means **CANCELLATION_REQUESTED**, not canceled.
- Exact-ID GET retains status, filled/remaining quantities and activity evidence.
  Pending/partial/filled states still need reconciliation. A known canceled,
  rejected or expired order clears order-state reconciliation only with an
  explicitly observed zero fill.
- Timeout, 5xx, unexpected success codes, missing Location, mismatched IDs,
  unrecognized statuses or malformed responses yield UNKNOWN and require
  reconciliation. 429 stops the operation. Redirects are never followed.
- A canceled order may already have partial fills. Inspect both activity evidence
  and fresh positions; record real price/time/fees only after confirmed execution.

After UNKNOWN or a lost response, inspect the existing attempt and broker order
state before considering a new attempt ID. A different ID does not supply broker
idempotency. There is no unattended retry, concurrency manager or automatic
fill-to-ledger conversion in this increment. stdout reports a saved result, not a
trade result; inspect the private JSON outcome and reconciliation fields.

## Testing without a real order

Synthetic transport/CLI tests exercise request paths and payloads, default-off
behavior, account/digest gates, intent persistence, duplicate attempts, rejects,
timeouts, accepted-then-partial outcomes, cancel/fill races and reservation failures.
They cannot prove broker entitlements, acceptance, fees, or production latency.

For a later **separately specified and authorized** live test:

1. Verify official endpoint/schema documentation, preview entitlement, order
   query completeness, App order limits and the selected account's permissions.
2. Use a broker preview before any submission; distinguish it from the local plan.
3. Re-read current shares, short calls and pending orders. A very OTM STO can
   still fill and is not permitted without free cover. Prefer the smallest
   explicitly reviewed limit order, with acceptable consequences if it fills.
4. Review exact account, contract, side, quantity, limit, fees, session and cancel
   timing. A low-price BTC on an existing short can be considered; it is not a
   promise of non-execution or a trade selected by this document.
5. Submit once, obtain the broker ID, query status, then request cancellation if
   still pending. Verify final state and any partial fill before reconciliation.

No live test was performed. Endpoint paths/order shape were checked against the
primary [schwab-py client source](https://github.com/alexgolec/schwab-py/blob/main/schwab/client/base.py)
and [CALL order templates](https://github.com/alexgolec/schwab-py/blob/main/schwab/orders/options.py).
The official Developer Portal required renewed login during development; its
current order schema and production entitlement were not re-verified.
Schwab's [covered-call guide](https://www.schwab.com/learn/story/covered-calls-beyond-basics)
describes its ordinary GTC lifetime; exact API query coverage remains separate.
