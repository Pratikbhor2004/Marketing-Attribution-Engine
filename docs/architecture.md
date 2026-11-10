# Architecture

1. `generator.py` creates reference/master entities first, then contacts, web sessions, leads, opportunities and append-only opportunity lifecycle events, followed by touchpoints, costs and orders.
2. IDs are deterministic and zero-padded. A single NumPy generator seeded by `DatasetConfig.seed` controls synthetic variation.
3. Valid baseline rows are generated before five explicit exception examples are injected. Exception rows retain `issue_type` labels and are catalogued in a separate ground-truth export.
4. Lead/session and opportunity/lead contact relationships are generated coherently. The one deliberately unsupported anonymous identity join is explicitly labelled and excluded from identity assertions.
5. `Order` is the recognized-revenue source of truth. Duplicate invoices are quarantined. Attribution uses valid orders and eligible touchpoints, excluding post-conversion events.
6. First-touch, last-touch and linear multi-touch are calculated independently. Linear allocations use integer cents and reconcile exactly per opportunity.
7. `campaign_roi` uses same-currency attributed revenue and valid same-currency spend. Foreign-currency revenue is exposed separately; no undocumented FX conversion is performed.
8. `validate_dataset` checks primary/composite keys, foreign keys, relationship consistency, event chronology, lifecycle state, date order, invoice duplication, order/opportunity reconciliation, attribution reconciliation and anomaly labels.
9. CSV, JSON, standalone SQL seed data and SQLite exports are generated from the same source tables. Ground truth is kept out of the analytical SQLite database.
10. `source_manifest.json` records the configuration, synthetic provenance and SHA-256 checksums for generated outputs.

## Important implementation note

This is a synthetic assessment prototype, not a production marketing platform. Production hardening would include timezone-aware timestamps, governed FX rate tables, consent enforcement, role-based access control, configurable attribution lookback windows, database-enforced constraints, and incremental/event-stream processing.
