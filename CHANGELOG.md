# Changelog

This project follows Semantic Versioning. Notable changes are documented in this file.

## [Unreleased]

### Added

- Two-layer follow-up classification: topics retain the main-question type while follow-ups store an independent probe focus.
- A current-report active-practice list that restores multiple drafts, generating sessions, and reviewing sessions after refresh.
- A product-manager-focused README with product decisions, screenshots, and a fast local onboarding path.

### Changed

- Standardized the public product name as Interview Review Assistant and removed the previous brand from the frontend and public documentation.
- Layered structural confidence and semantic notices for transcripts without speaker labels, so reliable follow-ups are no longer blocked by type ambiguity.

### Fixed

- Prevented timeline markers from entering questions and answers.
- Reduced follow-up type false positives and excessive splitting of consecutive speaker turns.
- Prevented practice-drawer polling from resetting the user's scroll position.

## [0.5.0] - 2026-08-20

### Added

- Report-level action practice with oral-answer, follow-up-drill, case-builder, and knowledge-quiz modes.
- Personalized practice briefs, draft persistence, multiple attempts, rubric feedback, factual-risk labels, and user-confirmed completion.
- `practice_sessions` and `practice_attempts` persistence, plus create, read, save, submit, and retry APIs.
- Deterministic practice and feedback in fixture mode; automated tests do not call a real model.

### Changed

- Added practice status and entry points to next-action cards, using a right-side drawer on desktop and a full-screen experience on mobile.
- Generated practice content lazily so it does not increase formal parsing or review time.
- Added one structured finalization attempt when real-model feedback submission fails; a second failure preserves the answer and allows retry.

### Fixed

- Fixed missing event-loop context when synchronous API routes started background practice tasks.
- Cascaded interview deletion to practice sessions, attempts, and aggregate status.
- Fixed native-dialog width constraints on mobile, practice polling races, and report navigation waits.

## [0.4.1] - 2026-08-20

### Added

- Server-side growth-action progress with status, practice notes, completion evidence, and self-assessment.
- Growth-plan read and action-progress update APIs, with current state merged into reports and trend views.

### Changed

- Improved information hierarchy, content expansion, status editing, and mobile layout for next-action cards.
- Unified action and score data sources across reports and growth trends to avoid client-side state drift.

### Fixed

- Fixed orphaned action progress after interview deletion, inconsistent recovery state, and several evidence-validation false positives.
- Fixed structured-review compatibility fields, action completion timestamps, and legacy report action-state reads.

## [0.4.0] - 2026-08-20

### Added

- Report V3 growth-plan final audit with structured findings, one targeted revision, second-round critical blocking, and warning publication.
- Read-only growth-audit tools, five SSE event types, a four-stage execution page, and separate audit summaries.
- Adaptive follow-up recognition and consecutive candidate-turn merging for transcripts without speaker labels.

### Changed

- Checkpoint recovery now reuses the latest growth-plan artifact and retries only incomplete revision or final-audit work.
- Public artifact metadata uses product role names without exposing underlying agent framework class names.
- Evidence review now supports topic concurrency, a structured fast path, prefetched evidence packets, and fewer tool calls for long interviews.
- The execution page now uses an evenly distributed four-stage progress track and preserves failed-stage recovery state.

### Fixed

- Added parsing for wrapped or double-encoded JSON and constrained invalid evidence IDs, turn IDs, and unsupported numbers.
- Fixed GrowthPlanner strength/risk count violations and unnecessary tool loops caused by repeated reads of large audit objects.
- Fixed failed runs reopening on the editing page, ineffective interview deletion, and stale error state after recovery.
- Fixed excessive splitting of continuous transcript turns, missed follow-ups, and source-reference false positives.

## [0.3.0] - 2026-08-19

### Added

- Report V2 with overall evaluation, capability gaps, next actions, and five-view topic analysis.
- Answer logic, interviewer signals, diagnosis, improved answer, and independent evidence views.
- Final growth-plan auditing, audit checkpoints, and fast recovery from audit timeouts.
- A dedicated self-introduction question type across parsing, verification, persistence, and formal review.
- Synthetic cross-role product-manager cases for long transcripts, multiple follow-ups, and missing speaker labels.

### Changed

- Replaced the forced one-action-per-day plan with task-focused actions, linked gaps, improvement dimensions, and completion criteria.
- Standardized question counts as main questions plus all follow-ups.
- Improved report, question-card, evidence-button, process-step, and mobile responsive layouts.
- Removed underlying agent framework branding from the frontend while preserving user-facing stages and execution status.

### Fixed

- Fixed template placeholders, field-type drift, unsupported numeric references, and string/object mismatches in structured topic submissions.
- Fixed invalid empty audits, duplicate submissions, execution timeouts, and GrowthPlanner recovery failures.
- Fixed stale errors after checkpoint recovery, report/trend score mismatches, and inconsistent question counts.
- Fixed missing follow-up source text, source-reference false positives, and incomplete cascading interview deletion.

## [0.2.1] - 2026-08-14

### Added

- Deepgram `nova-3` transcription for MP3, M4A, WAV, FLAC, and OGG.
- A chunked semantic parsing pipeline using a coordinator agent and worker agents.
- Dual-track raw segments, human edits, topics, follow-ups, and source locations.
- Asynchronous parsing states, SSE progress, retry handling, and versioned artifacts.
- Three synthetic cross-role interview cases and regression coverage for audio, parsing, question types, and workflows.

### Changed

- Upgraded New Review to mutually exclusive pasted-text, transcript-upload, and audio-upload sources.
- Grouped main questions and follow-ups by topic on the verification page, with segment correction and topic editing.
- Improved information hierarchy, evidence display, responsive layout, and actions in reports and interview records.
- Standardized Windows startup on UTF-8 to prevent Unicode agent logs from triggering GBK encoding errors.

### Fixed

- Fixed date-input formatting, oversized file-picker hit areas, record-list column alignment, and inconsistent action styles.
- Fixed edge cases involving low-confidence cards, follow-up expansion, report invalidation, and checkpoint recovery.

## [0.1.0] - 2026-08-06

### Added

- An interview-review MVP built with FastAPI, SQLite, and Vanilla JavaScript.
- Parsing, supervision, evidence analysis, quality audit, and growth-planning roles.
- TXT, PDF, and DOCX parsing with human question-card confirmation.
- Five-dimension scoring, evidence references, reflection audit, and a seven-day growth plan.
- SSE progress events, session recovery, redacted traces, fixture mode, Docker, startup scripts, and regression tests.

### Changed

- Improved desktop typography, process steps, file-upload interaction, and card spacing.
- Added toggle behavior for filling and clearing demo data.
