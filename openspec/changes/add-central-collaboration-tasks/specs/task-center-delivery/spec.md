## ADDED Requirements

### Requirement: Integrated task page with shared backend
The task UI MUST render directly inside the original admin application's task-management route, preserving its sidebar and header. It MUST NOT navigate to a second application, use an iframe, or render a duplicate sidebar. A shared task backend SHALL remain isolated from local WeChat processes and SHALL be reached through a same-origin API proxy.

#### Scenario: Local task entry
- **WHEN** a member opens task management in their own deployment
- **THEN** real task content appears inside the same application shell and URL origin without exporting local WeChat data

### Requirement: Cloud tasks are the default for local installations
Both development/preview servers and the packaged Docker frontend SHALL proxy task requests to `https://jqr.47jsy.com` by default. Members MUST NOT need a local task database or a separate website. Cloud failures SHALL remain visible failures, never a fallback to local tasks.

#### Scenario: Two independent local installations
- **WHEN** one local member publishes a task and another member opens task management in a different local installation
- **THEN** both read the same cloud task ID and changes propagate through the existing realtime channel without leaving their local pages

### Requirement: Local proxy session isolation
The local proxy MUST validate write Origin against its own origin before forwarding the unchanged Origin. The center SHALL accept its own public origin and standard loopback origins, retain CSRF and role checks, and issue a path-scoped HttpOnly local-client cookie separate from preview cookies. Public website sessions SHALL remain Secure. Non-task cookies and Authorization headers MUST NOT be forwarded to the center. CORS MUST NOT be opened to arbitrary websites.

#### Scenario: Local login and logout
- **WHEN** a member identifies from an HTTP loopback installation
- **THEN** the browser retains the cloud session for subsequent task requests and SSE, logout revokes it, and previous local preview credentials cannot authenticate it

#### Scenario: Foreign origin attempts a local write
- **WHEN** a request from another website or another local origin is sent to the local task proxy
- **THEN** that proxy rejects the write before contacting the center

#### Scenario: Task navigation and account isolation
- **WHEN** a member opens task details, navigates back, or signs out of task collaboration
- **THEN** routing uses the host router and task authentication does not clear host chat caches or redirect unrelated routes

### Requirement: Realtime updates and recoverable failures
Task changes SHALL be committed with durable events and replayable cursors. Clients SHALL refresh authoritative data, preserve known data on failures and reconnect after interruption. Draft events MUST remain private.

#### Scenario: Other member changes a task
- **WHEN** another member submits or updates work
- **THEN** an online authorized client sees the update without a manual page reload

### Requirement: Integrated responsive task layout
The original monochrome shadcn shell, search, theme and layout settings SHALL remain intact. A single task heading, primary creation action and compact identity area SHALL precede category navigation and grouped filters. Task lists and detail actions SHALL distinguish creator, lead and collaborators. Long titles and emails MUST NOT widen the page; narrow screens SHALL retain task status and essential identity information. Routine product pages MUST NOT show development-environment or synchronization implementation explanations.

#### Scenario: Narrow screen and long identity
- **WHEN** a member with a long email views tasks or opens a long task title on a narrow screen
- **THEN** labels wrap or truncate within their containers, category navigation scrolls within its own row, and task actions remain usable without page-level horizontal overflow

### Requirement: Preserve data while removing automatic integration
The new account migration MUST preserve the initial migration, task history and audit data. Previous GitHub sessions MUST NOT authenticate local accounts. Runtime code MUST NOT start a GitHub worker or expose OAuth and Webhook routes.

#### Scenario: Upgrade earlier prototype
- **WHEN** the new migration runs over existing prototype data
- **THEN** original records remain retained, the owner is initialized separately, and members enter using self-declared Git email

### Requirement: Authorized deployment boundaries
Implementation SHALL report local API/browser checks separately from cloud deployment. The authorized dig task service SHALL use its own persistent database, start without importing preview data, and preserve unrelated services. Git commit and push remain outside scope.

#### Scenario: Local verification succeeds
- **WHEN** local tests pass
- **THEN** delivery reports the verified scope without claiming a server release or GitHub integration
