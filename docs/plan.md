# LongArc — current investment scope

The active investment scope is covered calls on QQQ and IAU with separate per-symbol policies: infrequent manual trades, frequent observation and deterministic calculations, and complete records.

The current local source of truth is `private/qqq-covered-call-plan/README.md`, with the consolidated strategy and iteration plan in `PLAN.md` beside it. These personal planning files are intentionally excluded from Git; a fresh clone does not include them. Do not substitute the retired roadmap if they are unavailable.

The earlier SMA/momentum, multi-strategy and automated-execution roadmap is retired. Supporting more stock/ETF underlyings in the covered-call workflow does not revive that roadmap. It is recoverable in Git history and the local archive outside this project, not an active implementation backlog.

## Multiple-underlying extension

The existing capture, decision, execution and replay layers now carry explicit underlying symbols. QQQ and IAU share code and storage; policy identity, evidence, coverage and results stay separate. No schema migration or automatic adoption of new trading parameters is needed. Other stock/ETF CALL underlyings use the same interfaces only after their contract/source/policy requirements are established.

## Next work

SQLite is selected and the observation-journal foundation is implemented; see [storage operations](storage.md). Opening-lot and repeated-snapshot storage is implemented as a narrow initial relationship; see [holding tracking](holding-tracking.md). Explicit execution matching and roll-episode realized results are implemented; automatic broker reconciliation remains pending. Pure quote/scenario calculations and their audit adapter are implemented; see [calculations](calculations.md). Next define source freshness/units and a minimal policy contract, then reconcile manual fills. Any application-service lifecycle is distinct from database-file recovery.

Continue using the work packages and acceptance criteria in the local consolidated plan. Browser-assisted candidate collection, sparse descriptive history and fee-aware scenarios are implemented. Full domain storage, unattended full-chain collection, portfolio valuation, automatic execution reconciliation, alerts, and the complete workflow remain unimplemented. Deterministic advisory screening now consumes explicit policy and caller-verified facts. The remaining data utilities do not satisfy those requirements. Legacy trading configuration and placeholder commands have been removed; the new policy and persistence contracts will be implemented from the current plan.

## Next increments (planned, not implemented)

1. **Source and policy contract:** define acceptable quote/Greek ages, units, missing-data behavior, and user-approved thresholds. The current calculator reports raw ages and no recommendation.
2. **Execution reconciliation:** record manual actions/fills and current remaining quantities; connect partial closes, rolls and assignment to an episode without erasing history.
3. **Repeatable observation:** build the authorized collection loop, then scheduling and delivery checks. Keep scenario arithmetic distinct from actual position accounting.

The desired end state is one episode with action/fill history plus many price/Greek observations and calculation results. Neither a generic JSON field nor a self-reported quality label establishes verified transaction facts.
