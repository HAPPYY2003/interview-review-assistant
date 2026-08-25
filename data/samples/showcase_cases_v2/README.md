# High-Information-Density Showcase Cases V2

This directory contains five complete interview datasets for product demonstrations. Every transcript includes 15 question-and-answer turns with deliberate project context, individual actions, tradeoffs, quantified evidence, failures, and reflection. The cases are suitable for demonstrating topic grouping, evidence references, five-dimension scoring, quality audits, and growth planning.

## Privacy and Authenticity

- Candidates, former employers, customers, projects, metrics, and interview answers are synthetic and do not represent real people.
- Feishu, Meituan, Tencent Cloud, Microsoft, and AWS provide only publicly understandable target-role context.
- Synthetic job descriptions are rewritten from public websites, product documentation, and technical articles. They are not official current requirements.
- These cases must not be shared as genuine interview experiences or used to infer internal architecture or hiring criteria.

## Case Matrix

| Directory | Role | Primary focus | Input characteristics |
| --- | --- | --- | --- |
| `case-01-feishu-ai-product-manager` | Feishu AI Product Manager | Scenario value, agent workflows, evaluation, permissions, cost, and monetization | Speaker labels and timestamps |
| `case-02-meituan-strategy-analyst-asr` | Meituan Delivery Strategy Analyst | Metric trees, causal inference, regional experiments, and business influence | `speaker_0/1` ASR style |
| `case-03-tencent-cloud-sre` | Tencent Cloud SRE | SLOs, alerting, incidents, canary releases, capacity, and drills | System noise and incident follow-ups |
| `case-04-microsoft-product-designer` | Microsoft 365 Product Designer | Research, information architecture, accessibility, design systems, and data validation | Portfolio deep dive |
| `case-05-aws-customer-solutions-manager` | AWS Customer Solutions Manager | Cloud adoption, governance, executive communication, risk, FinOps, and scaling | Mixed Chinese and English terminology |

## Files in Each Case

- `profile.json`: company, role, interview round, review goal, and expected checkpoints.
- `job_description.txt`: synthetic job description.
- `resume.txt`: de-identified synthetic candidate resume.
- `transcript.txt`: high-information-density transcript with 15 turns.
- `sources.md`: public references and the boundary between fact, inference, and synthetic assumption.

## Recommended Demo Order

Start with Case 1 for a stable end-to-end review. Use Case 2 for unlabeled transcripts and human verification, Case 3 for noise exclusion and incident auditing, and Case 4 or 5 to demonstrate transfer beyond product-management roles.
