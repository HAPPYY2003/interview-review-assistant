# Complete Interview Review Test Cases

All content in this directory is fictional and contains no real personal information. It is intended for local product testing.

Each case contains:

- `profile.json`: company, role, interview round, date, review goal, and suggested checkpoints.
- `job_description.txt`: a synthetic job description that can be pasted or uploaded as TXT.
- `resume.txt`: a synthetic candidate resume that can be pasted or uploaded as TXT.
- `transcript.txt`: a synthetic interview transcript that can be pasted or uploaded as TXT.

## Cases

1. `case-01-product-manager`: evidence-rich answers with consecutive follow-ups, quantified results, and cross-functional decisions. Use it to test topic grouping and a strong report.
2. `case-02-data-analyst-asr`: an ASR-style transcript with `speaker_0` and `speaker_1` labels and several ambiguous answers. Use it to test speaker correction, low-confidence verification, and insufficient-evidence notices.
3. `case-03-backend-engineer`: device noise, technical follow-ups, and contradictions. Use it to test noise exclusion, follow-up impact labels, and quality auditing.

## How to Use

Open New Review, enter the metadata from `profile.json`, and upload or paste the job description, resume, and transcript. Use pasted-text or transcript-upload mode for these cases; Deepgram is not required.
