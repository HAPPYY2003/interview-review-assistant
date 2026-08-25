# Interview Review Assistant Showcase Cases

This directory contains five complete datasets for product demonstrations. Candidate names, former employers, projects, business metrics, and interview answers are synthetic. They do not represent real people and must not be shared as genuine interview experiences.

Real company names provide understandable public role context. Synthetic job descriptions are rewritten from public recruiting pages, product documentation, or technical articles and do not represent current official requirements. Each `sources.md` records the boundary between public fact, design inference, and synthetic assumption.

## Case Matrix

| Directory | Role | Input characteristics | Primary demonstration focus |
| --- | --- | --- | --- |
| `case-01-ai-product-manager` | Feishu AI Product Manager | Speaker labels and timestamps | Agent boundaries, evaluation, permissions, monetization, and failure review |
| `case-02-operations-data-analyst-asr` | Meituan Delivery Data Analyst | Unlabeled ASR style | Speaker inference, spoken-language noise, causal analysis, and experiment design |
| `case-03-cloud-backend-engineer` | Tencent Cloud Backend Engineer | Speaker labels and system noise | Microservice governance, incident follow-up, capacity, and data consistency |
| `case-04-enterprise-product-designer` | Microsoft 365 Product Designer | Two-interviewer context | User research, design systems, accessibility, and cross-functional conflict |
| `case-05-cloud-customer-solutions-manager` | AWS Customer Solutions Manager | Mixed Chinese and English terminology | Cloud migration, program governance, business value, and executive communication |

## Files in Each Case

- `profile.json`: interview metadata and suggested checkpoints.
- `job_description.txt`: synthetic job description.
- `resume.txt`: de-identified, fully fictional project resume.
- `transcript.txt`: synthetic transcript with approximately 15 question-and-answer turns.
- `sources.md`: public references, usage, and synthesis boundaries.

## Suggested Use

1. Enter company, role, round, date, and review goal from `profile.json`.
2. Paste or upload the job description, resume, and transcript. All files are TXT, so Deepgram is not required.
3. Case 2 intentionally omits interviewer/candidate labels and is suitable for speaker inference and human verification. The other cases are designed for stable main-question and follow-up grouping.
4. After report generation, inspect source traceability, follow-up impact, role fit, audit revisions, and next actions.
