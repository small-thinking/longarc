# LongArc — current investment scope

The sole active investment direction is the migrated QQQ covered-call plan: infrequent manual trades, frequent observation and deterministic calculations, and complete records.

The current local source of truth is `private/qqq-covered-call-plan/README.md`, with the consolidated strategy and iteration plan in `PLAN.md` beside it. These personal planning files are intentionally excluded from Git; a fresh clone does not include them. Do not substitute the retired roadmap if they are unavailable.

The earlier SMA/momentum, multi-strategy, multi-asset, and automated-execution roadmap is retired. It is recoverable in Git history and the local archive outside this project, not an active implementation backlog.

## Next work

SQLite is selected and the observation-journal foundation is implemented; see [storage operations](storage.md). Opening-lot and repeated-snapshot storage is implemented as a narrow initial relationship; see [holding tracking](holding-tracking.md). Full episode/roll reconciliation remains pending. Next add one pure midpoint/spread calculation and its logging adapter before connecting authorized live data. Any application-service lifecycle is distinct from database-file recovery.

Continue using the work packages and acceptance criteria in the local consolidated plan. Full domain storage, options collection, covered-call analysis, execution records, alerts, and the complete workflow remain unimplemented. The remaining data utilities do not satisfy those requirements. Legacy trading configuration and placeholder commands have been removed; the new policy and persistence contracts will be implemented from the current plan.

## Next increments (planned, not implemented)

1. **Episode tracking and input contract:** one stable identifier for a covered-call lifecycle; repeated observations link to it. Define contract identity, price units, nullable delta and separate market/receipt times. Preserve every check, including no-action and missing data. Schema changes and migration compatibility must be listed in that PR. This increment does not claim to implement fills, approvals, partial closes or roll accounting.
2. **One calculation and persistence adapter:** audit/reuse an existing calculation, validate inputs and test independent examples. Keep the calculation a pure function. A thin application method loads identified observations, calculates, and appends results with input IDs/hash, observation time and code/policy version. Repeated requests must remain idempotent.
3. **Expand only after the first loop works:** add additional metrics, then explicit manual action/fill records and reconciliation. A roll must retain the link to the original episode rather than erase earlier results. Authorized live data and scheduled collection require their own contracts and PRs.

The desired end state is one episode with action/fill history plus many price/Greek observations and calculation results. Neither a generic JSON field nor a self-reported quality label establishes verified transaction facts.
