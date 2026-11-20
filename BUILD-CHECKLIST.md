# WhiteHats prototype plan

The prototype follows one Saleor checkout dependency from an advisory to a verified fix.
A security analyst runs a live audit, reviews Gemini's cited assessment and sends a specific upgrade to the CISO for approval.
A fresh scan of the approved practice revision supplies the evidence for the board report.
The wider estate and monitoring incident use interactive, labelled simulations.

## User workflows

| When this happens | The person and the work they do | Handoff or evidence of completion |
| --- | --- | --- |
| **“A new advisory just landed. Are we affected?”** | The analyst opens the advisory, selects Checkout and its revision, and clicks **Run lightweight audit**. | The audit cites the actual lockfile, package version and advisory, gives the service owner a reason to act, and shows the run's time and AI cost. |
| **“Why did monitoring go quiet?”** | The SOC analyst opens the last-seen warning, inspects the affected asset and follows related alerts into one case. | A dated gap and recovery observation give the compliance team evidence of monitoring coverage. |
| **“Who will fix this, and can I approve it?”** | The analyst assigns the issue and submits a candidate revision with its comparison scan and the applicable business rule. | The CISO opens the same case in a second session, signs or rejects that exact candidate, and leaves a named, dated decision. |
| **“Did the change actually fix it?”** | After approval, the practice project selects the corrected revision and the analyst runs the same check again. | The issue closes only when the new result verifies the package change, with both revisions and scan IDs retained. |
| **“What can I give the auditor or the board today?”** | The CISO opens a framework, inspects the dated control evidence, and generates the board view beside the existing posture score. | A reviewer can retrieve the same evidence by ID and see which controls lack current evidence. |

Carry the same project, case ID, revision digest, finding fingerprint, owner and approval revision from queue to audit to report.
Keep the simulated monitoring incident in its own clearly marked case.
Persist the workflow server-side so the CISO sees the analyst's request after signing in and a repeated click leaves a single decision.
Give the presenter a reset that creates a fresh practice case and preserves earlier decisions and receipts.

## Two logins

| Login | Existing role and scope | What the person does |
| --- | --- | --- |
| Security analyst | Project `engineer` on Saleor only | Checks coverage, investigates the advisory, scans a revision, confirms the owner, reads policy and requests a fix. |
| CISO | `account_owner` plus project `approver` in each demo project | Sees all projects, settings, agent costs and the board report, then approves the exact revision or records a time-limited risk decision. |

Owner supplies account access and the project membership supplies approval rights.
The current board endpoint admits anyone with `see_project`, and `engineer` includes `edit_model`.
The API must require account-level access for the board report and settings privileges for configuration changes while preserving project-level triage.
Invite both named demo users, set unique passwords from the team's password manager, enroll their authenticators once, and prepare separate browser profiles.
Use ordinary MFA on each login.

## How the live audit works

**Start from the service.**
On the Audits page, the analyst selects Checkout, chooses a pinned practice revision and clicks **Run lightweight audit**.
The backend runs the existing pinned OSV-Scanner on the lockfile and looks up the installed package and advisory range in WhiteHats' local advisory database.

**Ask Gemini whether the service is affected.**
The backend sends Flash the package facts, advisory ranges and scanner evidence IDs.
Flash identifies the applicable advisory, explains the version match and proposes an upgrade for review.
The backend checks the cited IDs and version range before showing the finding.
The audit displays the revision, scanner version, advisory database date, model ID, elapsed time, returned token counts and estimated cost.

**Use the configured credential.**
The backend reads the Gemini key from the ignored local `.env` or the deployment secret store.
Use Vertex AI with a credential authorized for Vertex, or the Gemini Developer API with an AI Studio `GEMINI_API_KEY`.
Pin a Flash or Flash-Lite model after a successful request and display the provider that actually served it.
The key stays server-side, outside browser settings, scanner containers, logs and model inputs.
BigQuery uses separate, scoped credentials. [Gemini API key guide](https://ai.google.dev/gemini-api/docs/api-key) · [Vertex API key example](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/samples/googlegenaisdk-vertexai-express-mode) · [Token usage](https://ai.google.dev/gemini-api/docs/tokens)

**Verify the result on two revisions.**
The first live run must identify the affected package and link to the corresponding advisory and lockfile evidence.
The CISO reviews the candidate's comparison scan and signs its exact revision.
A changed candidate requires fresh approval.
After the simulated promotion, an independent live rescan must clear the same package match before the case closes.
Both runs retain their own run IDs, timestamps, revision digests and model receipts.
A scanner timeout, failed model call or unsupported citation leaves the audit incomplete and the finding open.

## Evidence for compliance and the board

SQLite keeps the decision and its evidence ID, and BigQuery stores the dated observation for retrieval by project and ID.
A failed delivery stays pending and a retry uses the same event key.
Rules evaluate the applicable vulnerability-management control using fresh, verified facts from that project.
Gemini can explain the result and suggest a next step.
Stale or missing facts appear as **Not checked**.

The board view pairs the existing 0-1000 posture score with separate readiness figures for DPDP, GDPR and SOC 2.
For each framework, show the count of applicable in-force controls passing on fresh live evidence alongside the total applicable controls in scope.
A zero denominator displays **Not assessed**.
Selecting a result opens the same evidence the analyst saw, including its date and the named decision.
Keep practice-project results and illustrative history identifiable throughout.
Every compliance view carries the approved wording, “This is WhiteHats' interpretation for your own review, not legal advice.”

## Live and simulated sources

Each result shows the source of its input, whether the check ran live, and when the evidence was observed.
A real scan of a practice lockfile should read **Live scan · Demo repository**.
A replay should read **Simulated · Recorded run, replayed** and retain its original timestamp.
Reuse the existing Sample badge, banner and Factory replay conventions.

| Dependency or data source | Default for judging | How it runs and switches |
| --- | --- | --- |
| Gemini Flash-class model | Switchable, live required for audit acceptance | Select the configured provider and model on the server, with recorded responses available for a visibly labelled rehearsal. |
| Model usage and cost | Real on live calls | Use returned token counts and a dated provider rate card for estimated cost, with recorded cost segregated and missing usage shown as unavailable. |
| Repository and manifests | Real scan of two pinned demo revisions | Use a sanitized Saleor-related practice lockfile and its corrected revision, pulled from the scoped GitHub App or staged locally by commit digest if GitHub is unavailable. |
| Local OSV advisory database | Switchable, live local lookup | Join scanner package/version evidence against the existing PyPI or npm mirror and show its last sync time, with a dated recording available offline. |
| OSV-Scanner | Real | Execute the existing pinned runner against the selected lockfile on each live audit and expose its result independently of the model. |
| Other managed scanners | Simulated for the wider fleet view | Display prior sanitized scanner evidence with its scanner/version/date and enable an installed licensed live runner through the existing scan catalogue. |
| CISA known-exploited catalog | Switchable, recorded | Load a dated catalog extract and offer a scheduled read-only refresh of the same format. |
| FIRST EPSS likelihood | Switchable, recorded | Load a dated score extract and offer a scheduled read-only refresh, preserving unknown scores. |
| AWS and CloudTrail | Switchable, recorded | Use the approved estate scenario in Studio and select the existing live read-only binding when the sandbox grant is available. |
| CloudWatch coverage | Switchable, recorded | Step through timestamped heartbeat observations or select an existing scoped CloudWatch reader. |
| Google Cloud inventory | Switchable, synthetic | Show one storage resource from the example estate or poll a customer-owned, dated inventory export through a scoped read-only binding. |
| Google Workspace identity | Switchable, synthetic | Show a sample directory observation or select a scoped customer-owned directory export for the existing identity checks. |
| Managed detection provider | Switchable, recorded | Group two sanitized alerts from a recorded export or poll a read-only customer export of the same shape. |
| Business knowledge and policies | Switchable, recorded | Cite one approved demo policy revision or select a live project sphere revision if AGE is configured. |
| Compliance controls | Real rules on dated inputs | Evaluate only the implemented control slice and distinguish assessed live facts, stale facts and scenario illustrations. |
| Fix Factory delivery and external merge | Simulated | Show the revision review and a clearly labelled practice promotion, with real human approval and a real rescan of the selected corrected revision. |
| BigQuery evidence | Switchable, live | Write and read one real audit evidence record in the demo dataset using separate scoped Cloud credentials, with a local recorded read available offline. |
| Historical trends | Switchable, synthetic backfill | Display illustrative months separately from actual audit dates and select measured history alone for the board's live numbers. |

Store per-project `source_mode` (`live`, `recorded`, `synthetic`) on the project/source binding in SQLite.
Use one reader per source contract and resolve the mode once before calling it.
A scheduled read of a customer export preserves the original observation time.
Where the live runtime is optional, enable its mode only after a successful capability check.
Store `WH_MODEL_MODE` (`live` or `recorded`), provider and model ID in deployment configuration.
Keep real calls, offline responses and fixtures traceable through every derived view.
Measured production improvements require fresh evidence from the live project.

## Browser walkthrough

Sign in as the analyst and follow a simulated monitoring gap from the Saleor map to its dated case.
Open the checkout advisory, run the live audit and inspect the package evidence and Gemini receipt.
Assign the finding and submit the candidate revision, then switch to the CISO login to review and approve it.
Advance the practice revision, run the verification scan and open its evidence from the board report.
Both logins should support the complete journey by keyboard at phone and desktop widths, including 390 px and 1440 px.

Repository references include the [product specification](../requirements/product-spec.md), [persona study and workflow sheet](../research/persona-value/PersonaValueMap.xlsx), [study priorities](../research/persona-value/BUILD-CHECKLIST.md), [north star](../research/persona-value/work/step0.md), [Saleor estate](../requirements/demo-ciso-plan.md), [compliance design](../requirements/compliance-sphere.md), [compliance implementation plan](../requirements/compliance-sphere-plan.md), [role grants](../platform/backend/app/permissions.py) and [deployment guide](../platform/deploy/aws/README.md).
