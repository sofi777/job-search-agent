## ADDED Requirements

### Requirement: Application status board
The system SHALL track every listing the user has acted on (applied, in-progress, rejected, offer, etc.) in one board, so no application is lost track of.

#### Scenario: Status change is reflected
- **WHEN** the user marks a listing's status (e.g. applied)
- **THEN** the board reflects the current status for that listing going forward

### Requirement: Web front end
The system SHALL expose the board as a simple web app, "DreamJobLanding" (`webapp/app.py`, Flask), kept short and simple (no database, no extra tools) per the project's `code` rule unless a requirement genuinely needs more.

#### Scenario: Landing page loads
- **WHEN** a visitor loads the web app's root page
- **THEN** they see the DreamJobLanding banner, name, tagline, and a call-to-action
