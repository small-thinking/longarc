# LongArc — current investment scope

The sole active investment direction is the migrated QQQ covered-call plan: infrequent manual trades, frequent observation and deterministic calculations, and complete records.

The current local source of truth is `private/qqq-covered-call-plan/README.md`, with the consolidated strategy and iteration plan in `PLAN.md` beside it. These personal planning files are intentionally excluded from Git; a fresh clone does not include them. Do not substitute the retired roadmap if they are unavailable.

The earlier SMA/momentum, multi-strategy, multi-asset, and automated-execution roadmap is retired. It is recoverable in Git history and the local archive outside this project, not an active implementation backlog.

## Next work

SQLite is selected and the observation-journal foundation is implemented; see [storage operations](storage.md). Next expand the audited domain and policy contracts, then build calculation methods against fixtures before connecting authorized live data. Any application-service lifecycle is distinct from database-file recovery.

Continue using the work packages and acceptance criteria in the local consolidated plan. Full domain storage, options collection, covered-call analysis, execution records, alerts, and the complete workflow remain unimplemented. The remaining data utilities do not satisfy those requirements. Legacy trading configuration and placeholder commands have been removed; the new policy and persistence contracts will be implemented from the current plan.
