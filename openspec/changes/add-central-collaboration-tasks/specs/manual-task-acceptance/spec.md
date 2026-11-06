## ADDED Requirements

### Requirement: Description and PR links only
The lead or owner SHALL submit a description and at least one safe HTTPS PR link. The center MUST store these values without fetching external content, checking repository membership, reading review state or evaluating merge state.

#### Scenario: External services unavailable
- **WHEN** the lead submits a correctly formed PR link while GitHub is unavailable
- **THEN** the center saves the submission and enters pending confirmation without any network call to GitHub

#### Scenario: Unsafe link
- **WHEN** a submission contains an executable URL, credentials or an unsupported link form
- **THEN** the center rejects it and preserves form input for correction

### Requirement: Owner manually confirms completion
Only the owner SHALL confirm a pending submission as completed or return it for changes. Returning MUST require an explanation; confirmation SHALL record the actor and time. Completing a task MUST NOT require any external merge event.

#### Scenario: Lead attempts completion
- **WHEN** a task lead calls the completion endpoint
- **THEN** the server rejects the operation even though that lead submitted the result

#### Scenario: Owner confirms
- **WHEN** the owner confirms the current pending result
- **THEN** the task becomes completed with an audit record of who confirmed it and when

### Requirement: Correction and historical results
A returned or withdrawn submission SHALL retain its description, links and decision. Resubmission SHALL create a new simple result record. Reopening a completed task MUST preserve the previous confirmation and require a new submission for another completion.

#### Scenario: Owner reviews stale result
- **WHEN** the owner confirms using a task version preceding withdrawal or resubmission
- **THEN** a conflict prevents accidentally confirming a different result

#### Scenario: Return and resubmit
- **WHEN** the owner returns a result and the lead resubmits corrections
- **THEN** the new result awaits manual confirmation and the earlier return reason remains visible
