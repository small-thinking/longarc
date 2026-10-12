# E*TRADE read-only API adapter

Checked against the official E*TRADE developer pages on 2026-10-12. This adapter
keeps each login in a named local profile (for example, `primary` or `secondary`). An
alias identifies a local configuration; it is not an E*TRADE login credential or
evidence that an individual API key works for multiple users. E*TRADE says an
individual key is tied to one user ID; a vendor key is the documented route for
multiple users. Sandbox and production require separate consumer keys.

The intended read-only command surface is `broker --profile <alias>
status|auth-start|auth-finish|accounts|positions|quotes|expiries|chain|ingest-chain`.
The broker adapter owns OAuth, GET requests, source envelopes, and conversion to
canonical option observations. Existing shared analytics, history, and storage
own calculations. These commands do not preview, place, change, or cancel orders.

## Authorization and environment

E*TRADE uses OAuth 1.0a. Obtain a request token, send the user to the authorize
page to log in and consent, then exchange the verifier for an **access** token.
Only the access token signs market and account requests. Every request includes
an OAuth timestamp within five minutes of UTC, a nonce, and HMAC-SHA1 signature.
The access token becomes inactive after two hours without API requests; the
renew endpoint reactivates it without issuing a replacement token. By default it
expires at midnight **US Eastern time**, after which a new browser login and
consent flow is required. Keep each profile's key, token, and environment in
ignored private files; never copy them into saved market envelopes, logs, Git,
or chat.

Market/account production URLs use `https://api.etrade.com/v1/`; sandbox uses
`https://apisb.etrade.com/v1/`. OAuth uses the documented production auth host
for both environments. Sandbox returns stored sample responses, not a complete
market simulation. A valid sandbox request can return symbols different from
those requested; no such result is a QQQ quote or QQQ chain merely because the
request named QQQ. Label sandbox observations `etrade_sandbox_synthetic`, separately from
production `etrade_api`, and reject request/response identity contradictions.

## Market data contract

| Read | Official endpoint / important shape | Adapter treatment |
| --- | --- | --- |
| Quotes | `GET /v1/market/quote/{symbols}`; `QuoteResponse.QuoteData[]`, each with `Product.symbol`, `All.lastTrade`, `dateTimeUTC`, and `quoteStatus` | Quote request can contain up to 25 symbols by default or 50 with `overrideSymbolCount=true`. Select the exact underlying; retain its source timestamp and delay status. |
| Expiries | `GET /v1/market/optionexpiredate?symbol=...`; `OptionExpireDateResponse.ExpirationDate[]` | Dates guide explicit chain requests. An expiry list does not prove strike coverage. |
| Chain | `GET /v1/market/optionchains?symbol=...`; `OptionChainResponse.OptionPair[]`, `SelectedED`, `timeStamp`, `quoteType` | Request the exact expiry and preserve the actual selected date. Defaults include `includeWeekly=false`, `skipAdjusted=true`, `optionCategory=STANDARD`, `chainType=CALLPUT`, `priceType=ATNM`; choose parameters deliberately. `noOfStrikes` can limit coverage. |
| Call | `OptionPair.Call` with `optionRootSymbol`, `optionType`, `osiKey`, `strikePrice`, `timeStamp`, bid/ask/size, volume, interest and optional `OptionGreeks` | Validate OSI root, call flag, expiry, and strike against request and selected expiry. Option timestamp is distinct from chain and capture clocks. |

The offline normalizer accepts saved envelopes with `provider=etrade-api`,
`resource=chain|quotes`, `captured_at`, `request`, and `data` containing one
official `OptionChainResponse` or `QuoteResponse` (the outer response key may
be present once). Chain request records `broker=etrade`, `login_alias`,
`environment`, `symbol`, and `expiry=YYYY-MM-DD`; quotes request records the same
profile identity and `symbols`. It returns the same canonical `symbol`,
`observed_at`, and `results` shape as the Schwab chain adapter, for shared
calculation and history code.

The chain documents `OptionGreeks.iv` but does not define a normalized IV unit
in the field table. Preserve the raw value with `iv_unit=provider_raw` until
unit evidence is established; do not silently combine it with fraction-valued
IV history. The chain does not establish a contract multiplier or deliverable
terms, so those remain unknown even for an apparently standard category.
Greek source time and touch/OTM probabilities are also absent from this chain
schema and remain null. Capture time never stands in for a source quote time.
The chain `quoteType` and underlying `quoteStatus` disclose delay independently.
`DELAYED` in either field marks the combined observation delayed; only an explicit
`REALTIME` chain type paired with a documented realtime underlying status marks
it not delayed. Missing, closing, or unrecognized statuses leave delay unknown.
The Greek `currentValue` flag is disclosed separately; false means not current,
and an absent flag leaves that property unknown. Delta outside [-1, 1] is
discarded as invalid rather than used for candidate comparisons.
Returned strikes are partial coverage, not a verified full listed chain.

E*TRADE documents the per-request quote symbol cap above. The official pages
checked here did not state a general market/account requests-per-minute or
requests-per-hour quota, so this runbook assigns none. On throttling or a
rate-limit response, stop the affected read and retain the error; do not
burst-retry or infer a quota from unrelated limits.

## Official references

- [Getting Started: keys and market data agreement](https://developer.etrade.com/getting-started/)
- [Developer Guides: OAuth lifecycle and sandbox behavior](https://developer.etrade.com/getting-started/developer-guides)
- [Authorization: access token](https://apisb.etrade.com/docs/api/authorization/get_access_token.html)
- [Authorization: renew token](https://apisb.etrade.com/docs/api/authorization/renew_access_token.html)
- [Market: quotes](https://apisb.etrade.com/docs/api/market/api-quote-v1.html)
- [Market: chains and expiry dates](https://apisb.etrade.com/docs/api/market/api-market-v1.html)
