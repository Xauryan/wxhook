## ADDED Requirements

### Requirement: Self-declared Git email entry
Members SHALL enter the task page by providing only a Git email address. No password, invitation, email verification or GitHub call SHALL be required. The UI MUST clearly identify the email as an unverified signature rather than authenticated ownership.

#### Scenario: First use
- **WHEN** a visitor submits a valid Git email
- **THEN** a regular member identity is created and the visitor enters task management inside the existing admin shell

#### Scenario: Returning identity
- **WHEN** the same email is submitted with different case or surrounding whitespace
- **THEN** the system normalizes it and reuses the same member ID and contribution history

### Requirement: Owner privilege is not derived from email
Entering an email MUST NOT grant owner permissions. Owner access SHALL remain tied to an existing trusted owner session or the operator's separately provisioned management credential. An email matching the owner record MUST NOT create an owner session.

#### Scenario: Impersonating owner by email
- **WHEN** a visitor enters an owner-like email or the email stored on an owner record
- **THEN** the visitor cannot acquire owner privileges such as completion, returning results or member administration; ordinary members may publish only their own tasks

#### Scenario: First member before owner setup
- **WHEN** someone enters an email before the operator configures the owner
- **THEN** that person remains a regular member, not the owner

### Requirement: Basic session and member controls
The center SHALL retain revocable sessions, same-origin write protection and request limits. Only the owner SHALL stop or restore a member identity. Stopping a member MUST revoke their current sessions while retaining task records.

#### Scenario: Disabled email
- **WHEN** a disabled email is entered again
- **THEN** entry is denied without deleting previous contributions

### Requirement: Retired account flow
The normal password-login, invitation creation and invitation acceptance routes MUST be removed. Historical accounts and invitation records MUST be retained without guessing email mappings or changing task ownership.

#### Scenario: Previous invitation link or API
- **WHEN** an old invitation or normal login endpoint is requested
- **THEN** it cannot create or authenticate a new member; the current page offers Git email entry instead
