## ADDED Requirements

### Requirement: Multi-source listing ingestion
The system SHALL ingest job listings from LinkedIn, Indeed, direct company career sites/ATS, and niche boards (e.g. Wellfound), not relying on any single source for coverage.

#### Scenario: Company-only posting is captured
- **WHEN** a job is posted only on a company's own career site (not syndicated to LinkedIn/Indeed)
- **THEN** it still appears in the user's ranked board

### Requirement: No missed best-fit postings
The system SHALL check sources frequently/broadly enough that a posting matching the user's preferences is not missed before its relevance window closes.

#### Scenario: New matching posting surfaces promptly
- **WHEN** a new posting matching the user's preferences appears on any covered source
- **THEN** it is surfaced to the user within one ingestion cycle
