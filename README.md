# Interview Review Assistant

> Turn an interview transcript into a review you can verify, act on, and practice from.

Interview Review Assistant is a local-first product for job seekers who want more than a generic AI summary. It reconstructs the interview, asks the user to confirm uncertain questions, links feedback to the original material, and turns recurring weaknesses into focused practice.

I created this project as a product management portfolio case: from problem framing and workflow design to usability iteration, quality evaluation, and release planning.

> **Language note:** Repository documentation is in English. The current product interface and sample interviews are Chinese-first because the initial target users are Chinese-speaking job seekers.

![Interview review report overview](docs/images/report-overview.png)

## Product at a Glance

| Area | Description |
| --- | --- |
| Target user | Job seekers who have an interview transcript or recording and want to improve across multiple interviews |
| Core problem | Important follow-ups are easy to forget, AI conclusions are difficult to verify, and advice often remains too generic to use |
| Product promise | Preserve the original interview, surface evidence-backed feedback, and provide a clear next practice step |
| My role | Product manager, product designer, and independent builder |
| Current stage | Working MVP with a safe demonstration mode and optional private review mode |

## The Problem

Most interview reviews depend on memory. Candidates usually retain one uncomfortable question or a vague feeling that an answer went badly, but miss the complete chain of questions, follow-ups, evidence, and interviewer signals.

Using a general AI chat tool improves speed but creates new risks:

- A follow-up may be treated as an unrelated question.
- Feedback may sound convincing without being supported by the transcript.
- The candidate's experience may be rewritten into facts that never happened.
- A long report may explain the problem without helping the user practice.
- Sensitive resumes, recordings, and interview content require clear privacy boundaries.

The product therefore focuses on one principle:

> AI can help interpret the interview, but the user should be able to verify the source and retain control over uncertain decisions.

## The Experience

```text
Add interview material
→ Confirm reconstructed questions and follow-ups
→ Review evidence-backed feedback
→ Identify repeated skill and knowledge gaps
→ Choose the next action
→ Practice and receive structured feedback
```

### 1. Add Interview Material

Users can paste a transcript or upload text, documents, and prerecorded audio. The product supports both structured transcripts and noisier speech-to-text material.

Private material remains on the user's computer by default. Audio is only sent for transcription after the user explicitly confirms consent and de-identification.

### 2. Confirm Questions Before Review

The product reconstructs:

- Main interview questions.
- Candidate answers.
- Follow-up questions and their relationship to the main topic.
- Observable uncertainty such as unclear speakers, missing answers, or questionable boundaries.

Users review the uncertain parts before the final analysis begins. They can correct content, change a speaker, merge related topics, split a follow-up into a new topic, or remove noise.

This step prevents an early parsing mistake from contaminating the entire report.

### 3. Read an Evidence-Backed Report

The report includes:

- An overall interview evaluation.
- Five dimensions: relevance, structure, evidence, analytical depth, and role fit.
- Skill and knowledge gaps linked to the questions that revealed them.
- Topic-by-topic views of answer logic, interviewer signals, problem diagnosis, improved wording, and source evidence.
- Quality-review notes that explain whether important risks were found or corrected.

Every important conclusion can be traced back to the interview, resume, or job description. Improved answers reorganize confirmed facts and mark missing information instead of inventing experience.

### 4. Turn Feedback into Next Actions

The product converts repeated weaknesses into a short list of next actions. Each action explains:

- Why it matters.
- Which gap and interview question it came from.
- What the user should work on.
- What "done" looks like.

Actions are not forced into a "one task per day" schedule. Users can choose the most valuable next step based on their next interview and available time.

### 5. Practice Inside the Report

Each action can open one of four practice modes:

| Mode | Best for |
| --- | --- |
| Oral answer | Explaining an experience clearly and concisely |
| Follow-up drill | Preparing for deeper questions about contribution, evidence, and decisions |
| Case builder | Completing missing context, process, metrics, and reflection |
| Knowledge quiz | Checking role-specific concepts and methods |

Drafts are saved locally. Users can leave, return, submit another attempt, compare feedback, and decide when an action is complete. New facts introduced during practice are marked for verification and never change the original report automatically.

![Action practice drawer](docs/images/action-practice.png)

## Key Product Decisions

| Product decision | Why it matters |
| --- | --- |
| Confirm the reconstructed interview before analysis | An incorrect question-answer structure would weaken every later conclusion |
| Preserve the source separately from user edits | Users can correct transcription problems without losing the original record |
| Require evidence for important judgments | Plausible feedback is not always trustworthy feedback |
| Keep scoring rules consistent | The same performance should not receive a different total score because of wording variation |
| Review both the diagnosis and the action plan | A good diagnosis can still lead to generic or unsupported advice |
| Add practice at the point of action | Reading feedback alone rarely creates behavior change |
| Store private material locally by default | Interview transcripts often contain personal and company-sensitive information |

## A Typical Product Walkthrough

For a portfolio interview or product review, the recommended demonstration takes about five minutes:

1. Start with a prepared synthetic interview so no private information is exposed.
2. Show how main questions and follow-ups are reconstructed.
3. Correct one intentionally uncertain item to demonstrate user control.
4. Open a completed report and trace one diagnosis back to its source.
5. Show how the same issue becomes a concrete next action.
6. Open a practice session, submit a short response, and review the feedback.

This walkthrough demonstrates the complete user value loop without waiting for a live review to finish.

## Try It Locally

The default demonstration mode uses synthetic material and does not require an AI service key.

### Option A: Docker

Prerequisite: [Docker Desktop](https://www.docker.com/products/docker-desktop/)

```bash
git clone https://github.com/HAPPYY2003/interview-review-assistant.git
cd interview-review-assistant
docker compose up --build
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

### Option B: Python 3.11

Windows PowerShell:

```powershell
git clone https://github.com/HAPPYY2003/interview-review-assistant.git
cd interview-review-assistant
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\start.ps1
```

macOS or Linux:

```bash
git clone https://github.com/HAPPYY2003/interview-review-assistant.git
cd interview-review-assistant
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Then:

1. Open **New Review**.
2. Select the demo-data action or use synthetic material from `data/samples/`.
3. Confirm the question cards.
4. Open the report and start one practice session.

To use your own private AI service, follow the comments in [`.env.example`](.env.example). Keep the key on the server and never commit `.env`.

## Privacy and Responsible Use

- The demonstration data is synthetic.
- Personal transcripts, recordings, reports, and practice history are excluded from Git.
- Private materials are stored locally by default.
- Users must obtain permission before uploading another person's recording.
- A deleted interview also removes its related review and practice records.
- The product does not estimate hiring probability or claim to predict an employer's decision.

For friends, interviewers, or evaluators, use demonstration mode and synthetic data on a trusted machine.

## Product Iteration and Quality Evaluation

The product is improved through a repeatable loop:

```text
Observe a failed or costly user case
→ classify the root cause
→ define one improvement hypothesis
→ test representative development cases
→ run the full regression set
→ record the version and remaining risks
→ validate the stable version on unseen cases
```

The question-reconstruction evaluation focuses on five primary measures:

- Completion rate.
- Question identification quality.
- Answer assignment quality.
- Follow-up relationship quality.
- Weighted user correction effort.

Evidence fidelity, unsafe pass-through, false alarms, time, and usage cost are retained as quality safeguards.

The project includes synthetic examples and a repeatable evaluation process. Simulated reports are used only to test the reporting format; published performance claims must come from authorized, anonymized, and previously unseen cases.

## Current Evidence and Boundaries

What the current project demonstrates:

- A working end-to-end experience from transcript to practice.
- Synthetic scenarios covering different roles, transcript formats, follow-ups, and failure states.
- Automated checks for important product paths and known regression risks.
- Recoverable failed reviews and persistent practice drafts.
- Versioned product iterations and documented release history.

What the project does **not** yet claim:

- Production-scale adoption.
- Validated hiring-outcome prediction.
- Accuracy results from private data that has not been formally labeled and frozen.
- Multi-user cloud security or account isolation.

## Roadmap

### Next

- A practice center that brings together drafts, feedback, and attempt history across interviews.
- Practice progress in growth trends while keeping it separate from interview scores.
- Spoken-answer practice with pacing and delivery feedback.
- Optional account-based deployment with clear deletion controls and opt-in usage analytics.
- Larger user testing focused on action completion, return usage, and repeat interview value.

### Intentionally Out of Scope for the Current MVP

- Live interview recording.
- Automatic job applications.
- Hiring-probability predictions.
- Multi-user collaboration.

## What This Portfolio Demonstrates

1. **Problem framing:** translating a vague "AI interview summary" request into a verifiable user journey.
2. **Human-centered AI design:** deciding where the product should automate, ask for confirmation, or preserve uncertainty.
3. **Information architecture:** making evidence, scores, gaps, actions, and practice understandable in one workspace.
4. **Failure experience design:** helping users recover without repeating expensive work.
5. **Metric-driven iteration:** connecting Bad Cases to a hypothesis, a measurable change, and a release decision.
6. **Responsible product boundaries:** protecting private material and avoiding unsupported hiring claims.

See [CHANGELOG.md](CHANGELOG.md) for release history.
