# E*TRADE order capability and options approval

E*TRADE provides order listing, preview, placement, cancellation and change APIs.
The local adapter now supports a single CALL leg, LIMIT price, regular session
and DAY duration. Sell to Open maps to `SELL_OPEN`; Buy to Close to `BUY_CLOSE`.
This capability is separate from account approval and from enabling live trading.

## Available local workflow

Use the same named profiles, login credentials and exact account bindings as
[ordinary broker reads](brokers.md). No duplicate account scripts or new keys
are needed. Both brokers share CALL intent/price validation and the single-attempt
HTTP transport. Existing Schwab order commands and live gates are unchanged.

Save an owner-only JSON intent under `private/`, using the same input as the
Schwab planner, for example:

```json
{
  "option_symbol": "QQQ   261030C00795000",
  "instruction": "SELL_TO_OPEN",
  "quantity": 1,
  "price": "1.25"
}
```

This is a synthetic shape example, not a current quote or recommendation.

```bash
uv run python -m longarc.cli broker --profile etrade-primary order-plan \
  --file private/call-intent.json --client-order-id UniqueExample01 \
  --operation preview --output private/call-plan.json
uv run python -m longarc.cli broker --profile etrade-primary orders \
  --output private/orders.json
uv run python -m longarc.cli broker --profile etrade-primary order-status \
  --order-id 12345 --output private/order-status.json
```

`order-plan` never loads app keys/tokens, renews OAuth or contacts E*TRADE. It
supports `preview`, `place`, `change-preview`, `change-place` and `cancel` plans.
Changes/cancellations require an existing order ID. CALL plans require a unique
1–20 character alphanumeric client order ID. Plan digests include the operation,
login, environment, account and normalized order terms. Files stay private and
cannot overwrite existing evidence or credential filenames.

`orders` and `order-status` explicitly issue GET requests with writes disabled.
They retain account-scoped raw details, provider statuses, partial quantities
and actual execution-price fields when present. These observations do not import
fills into the execution ledger. Missing quantities remain unknown. An order
missing from the default query window is unavailable, not cancelled. Repeated,
foreign or truncated pages fail closed; HTTP 204 is preserved as incomplete
evidence rather than a verified empty open-order set. Complete pagination applies
only to the broker's returned default window, not all historical orders.

## Order transport contract

`longarc.etrade.orders.ETradeOrderClient` implements:

| Operation | Method and account-scoped suffix |
| --- | --- |
| List/status | GET `/orders`; exact status selected locally from returned pages |
| New preview | POST `/orders/preview` |
| New placement | POST `/orders/place` |
| Change preview | PUT `/orders/{orderId}/change/preview` |
| Change placement | PUT `/orders/{orderId}/change/place` |
| Cancel | PUT `/orders/cancel` with `CancelOrderRequest.orderId` |

OAuth signing includes the actual HTTP method. JSON request bodies are not form
parameters in the OAuth signature. Sandbox and production hosts follow the
selected login config; tokens must match consumer key/login/environment.

Writes, including preview, require an explicitly enabled adapter. **The CLI has
no live-write switch and never enables it.** Tests enable injected synthetic
transports only. A successful preview must echo the exact account and requested
CALL/quantity/action/limit terms before it can produce an executable receipt.
Messages require review. Placement uses identical terms and the preview ID
within three minutes; a receipt cannot be moved between clients/accounts or
changed by mutating returned data. The receipt is consumed before dispatch,
including timeouts. Replacements require an observed open/unfilled single leg,
retain the original contract/action and repeat status observation before place.

HTTP failures, redirects, throttling and malformed/mismatched responses are not
retried. An accepted placement is not a fill; a cancellation request is not a
confirmed cancellation. Reconciliation remains required, including terminal
orders with fills. In-memory duplicate guards are transport safeguards, not a
durable cross-process execution journal.

Production execution is deliberately unavailable through the CLI. Before adding
it, reuse/extend the existing [Schwab order procedure](schwab-orders.md): explicit
owner authorization, current broker options approval, fresh per-account shares,
existing/pending options and stock-sale reservations, cash/debit caps, market
hours, quote clocks/delay, verified standard deliverables, durable attempts and
fill reconciliation. OSI validation alone does not prove a 100-share deliverable;
the current E*TRADE chain lacks those terms. No switch or local option-level
setting can replace these checks. This increment adds the tested transport and
planning/observation interface, not unattended production execution.

## Getting Level 1

API keys and OAuth consent do not grant options approval. The balance API's
`BalanceResponse.optionLevel` distinguishes `NO_OPTIONS`, `LEVEL_1` and higher
levels. A fresh balance read can verify the result after broker approval.

The official [Options page](https://us.etrade.com/what-we-offer/investment-choices/options)
lists covered calls, buy-writes and covered-call rolling under Level 1. Its
[Apply now link](https://us.etrade.com/e/t/mou/application?upgrade=2) opens the
options application for an existing brokerage account after login. Each owner
selects the intended Individual Brokerage account, requests the covered-position
level, supplies their actual financial/experience information, reviews the
agreements and submits the application. Separate accounts/logins require their
own approval; an API app cannot approve or upgrade them. Broker approval must
be verified after submission. Levels 3 and 4 require margin approval; covered
calls do not require those levels.

## Verification boundary

Synthetic transports cover payloads, method-sensitive OAuth signatures,
account/environment isolation, preview expiry/binding, partial-fill replacement
races, cancellation semantics, pagination and uncertain responses. Owner-authorized
production GET order-list requests reached the API and returned HTTP 204 without
order details; that does not verify full order-query coverage. No real broker
preview, placement, change or cancellation was tested. Sandbox samples remain
synthetic and cannot establish production entitlement or actual fills.

Official contracts: [Order API](https://apisb.etrade.com/docs/api/order/api-order-v1.html),
[Balance API](https://apisb.etrade.com/docs/api/account/api-balance-v1.html).
