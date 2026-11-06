## ADDED Requirements

### Requirement: Missing source databases must not abort snapshot build
Building the local snapshot cache SHALL tolerate missing source databases. A missing or unopenable source file MUST be recorded as an absent fingerprint entry and as a zero count instead of aborting the build. An absent source MUST NOT prevent application startup.

#### Scenario: Message media database absent
- **WHEN** the selected data root has no `message/media_0.db`
- **THEN** the cache fingerprint is computed with that source recorded as unknown size and modification time
- **AND** the count for that database is reported as `0`
- **AND** the application reaches startup completion and `GET /api/health` and `GET /api/overview` respond with `200`

#### Scenario: Table absent inside an existing database
- **WHEN** a source database exists but the queried table is not present
- **THEN** only that count is reported as `0` and the other counts from the same database are unaffected

### Requirement: Complete data roots keep identical results
When every configured source database is present, source counts and fingerprint inputs SHALL be identical to the previous implementation, so cached snapshots are not rebuilt purely by this change.

#### Scenario: Regression on a complete data root
- **WHEN** the cache is rebuilt from a data root containing all configured source databases
- **THEN** every source count equals the value produced before this change
