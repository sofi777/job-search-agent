## ADDED Requirements

### Requirement: Fit scoring against resume and preferences
The system SHALL score each ingested listing for fit against the user's resume and stated preferences, so highly relevant listings are distinguishable from noise.

#### Scenario: Listing is scored on ingestion
- **WHEN** a new listing is ingested
- **THEN** it receives a fit score and reason before being shown to the user

### Requirement: Ranking improves from user actions
The system SHALL adjust future ranking based on the user's actions on past listings (e.g. applied, skipped, saved).

#### Scenario: Repeated skips shift future ranking
- **WHEN** the user consistently skips listings sharing a trait (e.g. a seniority level, industry)
- **THEN** future listings sharing that trait are ranked lower
