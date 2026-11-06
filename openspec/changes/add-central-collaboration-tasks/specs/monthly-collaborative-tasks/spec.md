## ADDED Requirements

### Requirement: Shared monthly task pool
Members SHALL create tasks with month, objective, acceptance criteria, deadline and priority. The creator or owner SHALL edit and publish them. All authorized members SHALL see the same published tasks. Historical tasks MUST persist across month boundaries. Drafts and their private events SHALL be visible only to their creator and the owner.

#### Scenario: Member creates and returns to a draft
- **WHEN** a member creates a task, including outside the current month or list filter
- **THEN** the server records that member as creator, returns the draft detail, and the member can find it under My created tasks after returning or reloading
- **AND** another member cannot list, read, edit or publish that draft or inspect its private activity

### Requirement: Creator is independent of execution roles
Creation SHALL record an immutable server-assigned creator separately from the lead and collaborators. Creating a task MUST NOT assign a lead, join collaboration, or grant owner privileges. Existing creators SHALL be recovered only from recorded creation or copy events; missing attribution SHALL remain unknown rather than inferred from current ownership.

#### Scenario: Another member leads the creator's task
- **WHEN** another member claims a published task
- **THEN** that lead may submit results but cannot edit the task scope; the creator may edit scope while no result is pending
- **AND** only the owner may confirm completion, return results, reassign responsibility, cancel, reopen or manage member access

#### Scenario: Owner copies an existing task
- **WHEN** the owner copies a task to another month
- **THEN** the new draft records the copying owner as its creator, without copying execution participants

#### Scenario: Month changes
- **WHEN** an unfinished task crosses a month boundary
- **THEN** its original records remain available without automatic reset

### Requirement: One lead and multiple collaborators
An executing task MUST have exactly one lead and MAY have multiple collaborators. Claims SHALL be atomic; transferring responsibility SHALL require target acceptance unless the owner explicitly reassigns with a reason.

#### Scenario: Concurrent claim
- **WHEN** two members claim an unassigned task simultaneously
- **THEN** one succeeds and the other receives a conflict

#### Scenario: Collaborator leaves
- **WHEN** a collaborator leaves a task
- **THEN** past work remains recorded and the current lead is unchanged

### Requirement: Work allocation and discussion
Leads and owners SHALL assign lightweight work items. Collaborators SHALL only update their own work progress, while authorized members can discuss published work. Work item completion MUST NOT imply overall task completion.

#### Scenario: All work items checked
- **WHEN** every work item is marked done but the owner has not confirmed the submitted result
- **THEN** the task remains incomplete

### Requirement: Audited concurrent writes
Task mutations SHALL check actor permissions, expected version and idempotency. Scope changes during pending review MUST require withdrawal first. Cancellation or reopening SHALL be owner actions with a reason.

#### Scenario: Duplicate submission retry
- **WHEN** the same write is retried with the same key and body
- **THEN** the original result is returned without duplicating work
