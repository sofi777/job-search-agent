## ADDED Requirements

### Requirement: Tailored cover letter drafting
The system SHALL draft a cover letter per listing, tailored to that role and grounded in the user's real achievements (resume/past application history), not generic filler.

#### Scenario: Cover letter cites real achievements
- **WHEN** a cover letter is drafted for a listing
- **THEN** it references specific achievements from the user's actual resume/history relevant to that role

### Requirement: Application question drafting
The system SHALL draft answers to a listing's application questions in the user's authentic voice.

#### Scenario: Draft answer produced for a custom question
- **WHEN** a listing has custom application questions
- **THEN** the system produces a draft answer for each, grounded in the user's real experience

### Requirement: Voice improves from user edits
The system SHALL use the user's edits to prior drafts to make future drafts sound more like the user, over time reducing the edits needed.

#### Scenario: Recurring phrasing fix is learned
- **WHEN** the user repeatedly makes the same kind of correction across drafts
- **THEN** future drafts apply that correction without being asked again
