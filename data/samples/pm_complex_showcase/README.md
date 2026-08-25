# Complex Feishu AI Product Manager Interview Case

This directory contains synthetic material for product demonstrations and regression testing. Real public company and product names provide understandable context; the job description is reorganized from public information. The candidate, employer, projects, business metrics, and interview content are entirely fictional and do not represent ByteDance or Feishu hiring decisions.

## Files

- `profile.json`: company, role, interview round, date, and review goal.
- `job_description.txt`: a synthetic job description based on public recruiting and product information.
- `resume.txt`: a fictional project-based resume with strong role fit and deliberate risks for deeper follow-up.
- `transcript_with_roles.txt`: 20 question-and-answer turns with timestamps and explicit interviewer/candidate labels.
- `transcript_without_roles.txt`: the same content and order with speaker labels removed, for speaker-inference and confidence testing.
- `sources.md`: public references and the boundaries of their use.

## Recommended Demo

1. Enter the metadata from `profile.json` on New Review.
2. Upload or paste `job_description.txt` and `resume.txt`.
3. Start with `transcript_with_roles.txt` to demonstrate stable parsing, follow-up grouping, evidence references, and report generation.
4. Then use `transcript_without_roles.txt` to demonstrate confidence notices, speaker correction, and human verification.
5. When comparing reports, explain how input quality changes verification effort. Do not claim perfect recognition for transcripts without speaker labels.

## Expected Checkpoints

- Both transcripts contain 20 question-and-answer turns in the same order.
- The labeled version should identify 20 questions and group several consecutive follow-ups into topics.
- The unlabeled version contains no interviewer, candidate, Q, or A markers; speaker roles and boundaries should be presented for verification.
- The report should identify evidence related to enterprise AI, agent workflows, evaluation, permissions, growth, and monetization.
- The audit should examine small samples, unsupported renewal attribution, an automatic-write incident, and metric-definition boundaries.

## Usage Boundary

This case is only for product demos, regression testing, and interview practice. It must not be presented as a real candidate profile, an official job description, or internal ByteDance/Feishu information.
