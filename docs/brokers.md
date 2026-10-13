# Named broker and account access

Use one `broker` command for Schwab and E*TRADE. A profile selects the broker,
local login alias, credential directory, environment, and one explicitly bound
securities account. Separate accounts never combine share coverage or cash.
The same analytics, execution ledger and reports operate on canonical inputs.
Separate [E*TRADE order commands](etrade-orders.md) provide offline limit CALL
plans and explicitly requested GET order observations using the same profiles.
They do not enable production order writes. Schwab keeps its existing separate
order commands and gates.

## Local configuration

Profiles live in ignored, owner-only `private/brokers.json`. Existing Schwab
credentials remain in `private/schwab/`. E*TRADE has separate directories for
each login and environment, such as `private/etrade/logins/primary/production/`.
An individual consumer key is tied to one login, which can contain multiple
securities accounts. Never mix login tokens or sandbox/production keys.

```bash
uv run python -m longarc.cli broker --profile schwab-main add-profile \
  --broker schwab --login main --environment production --directory private/schwab
uv run python -m longarc.cli broker --profile etrade-primary add-profile \
  --broker etrade --login primary --environment production \
  --directory private/etrade/logins/primary/production
uv run python -m longarc.cli broker profiles
uv run python -m longarc.cli broker --profile etrade-primary status
```

Add another alias with `--login secondary` and its own directory. Sandbox aliases
use `--environment sandbox`. An optional `--expected-account-last4` adds a suffix
constraint; an exact account key is still required. Public examples are not
private account mappings. `status` checks configuration and token-file presence
without OAuth or broker requests; presence does not establish token validity.

## Authorization and account binding

```bash
uv run python -m longarc.cli broker --profile etrade-primary auth-start
# Open the saved private authorization URL; the owner reviews consent.
uv run python -m longarc.cli broker --profile etrade-primary auth-finish
# Enter the displayed verifier at the hidden terminal prompt, never in chat.
uv run python -m longarc.cli broker --profile etrade-primary accounts \
  --output private/primary-accounts.json
uv run python -m longarc.cli broker --profile etrade-primary bind-account \
  --accounts-file private/primary-accounts.json
```

Binding prompts for the exact key from the saved inventory. It checks profile,
login/environment, status and optional suffix, and saves key and account ID.
There is no account-index or first-account fallback. `positions` and `balance`
stop before network traffic without binding; returned account IDs must match
before data is saved as that account's holdings or balance.

E*TRADE uses OAuth 1.0a and needs browser authorization after default token expiry
at Eastern midnight. Two-hour inactivity is renewable within the existing day;
each requested read performs one explicit renewal before data requests. This
differs from Schwab's refresh-token flow. API access and options approval remain
separate. See [E*TRADE contracts](etrade-readonly.md) and
[Schwab setup](schwab-readonly.md#local-python-api-access). Existing `schwab
auth-listen` remains the automatic loopback workflow; the common interface also
supports its manual callback fallback.

## Shared snapshots and analysis

```bash
uv run python -m longarc.cli broker --profile etrade-primary positions
uv run python -m longarc.cli broker --profile etrade-primary balance
uv run python -m longarc.cli broker --profile etrade-primary quotes --symbols QQQ IAU
uv run python -m longarc.cli broker --profile etrade-primary expiries --symbol QQQ
uv run python -m longarc.cli broker --profile etrade-primary chain \
  --symbol QQQ --expiry YYYY-MM-DD --output private/primary-chain.json
uv run python -m longarc.cli broker --profile etrade-primary quotes \
  --symbols QQQ --output private/primary-quotes.json
uv run python -m longarc.cli broker --profile etrade-primary ingest-chain \
  --db private/longarc.sqlite3 --chain-file private/primary-chain.json \
  --quotes-file private/primary-quotes.json
```

The same commands accept a Schwab profile; omit `--expiry` for its returned
multi-expiry chain. E*TRADE requires an exact expiry. Returned strikes or dates
do not prove full listed-chain coverage; no automatic sweep is performed.
E*TRADE pagination retains raw pages and requires explicit completion metadata.
Malformed pages, conflicting accounts and repeated positions fail.

Snapshots retain raw data plus normalized `broker-accounts-v1`,
`broker-positions-v1`, `broker-quotes-v1` or `broker-expiries-v1` views. Requests
carry profile/login/environment/account identity. Only new private output files
are allowed; credential filenames are reserved. Quotes use integer USD
micro-units and independent source clocks. Unknown quantity direction, delay,
timestamps, contract terms or IV units remain unknown.

Chain imports share storage, metrics and idempotence code. Existing Schwab
imports and record IDs remain compatible. Sources are `schwab_api`, `etrade_api`,
and `etrade_sandbox_synthetic`; sandbox mode is `shadow`, quality `synthetic`.
The importer checks labels against evidence environment, including library calls.
Sandbox examples are not current QQQ data or a complete paper simulator.

Reuse `options history`, `options iv-history`, `options probabilities`, `calc`,
and account-scoped `options position-report`/`options performance`. Use distinct
ledger aliases for actual securities accounts; do not rename or merge history
implicitly. Positions and averages are not fills; execution matching remains
explicit. E*TRADE IV units remain `provider_raw`, so fraction-IV history/model
inputs cannot silently consume them. No fee or trading-policy changes are made.

Legacy `schwab` commands remain compatible, including their separately gated,
default-disabled order capability. E*TRADE order submission is not implemented
here. Shared commands never preview, submit, replace or cancel orders and do not
change recurring review schedules.

## Maintained references

The [official Getting Started page](https://developer.etrade.com/getting-started)
is publicly readable. The local runbook records endpoint/field contracts,
authentication expiry, missing units and official links with a checked date.
A full website dump is unnecessary and would not become the live authority.
Recheck official sources when changing interfaces. Authenticated pages,
credentials and personal snapshots never belong in Git.
