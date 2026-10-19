I have everything needed. Findings below.

**Grounding audit (summary).** Every date, employer, title and number in cv.tex checks out against profile.md. In cover.tex, three items aren't backed: the mention of "Claude Code" (nowhere in the profile), the sentence about the "fit assessment" gap (an internal artifact, not something a candidate knows or can explain to a hiring manager), and the "full-time, at most 40 hours" availability line (a preference, and a deal-breaker, not something to volunteer to an employer). "I own a Kafka pipeline" overstates the profile's "owns the shipment-events pipeline". Nothing else in either file is escalated or invented.

## PART A

```json
[
  {
    "file": "cover.tex",
    "old_string": "The fit assessment also flags data engineering experience as a gap, although my profile records seven years building retail and logistics pipelines. I would welcome clarifying that mismatch. My profile documents no experience with AI/ML engineering, LLM-based applications or \\mbox{Claude Code}; tested datasets and repeatable ingestion provide adjacent foundations for machine learning enablement.",
    "new_string": "Across seven years in data engineering I have not worked with AI/ML engineering or LLM-based applications, but tested datasets and repeatable ingestion give me a practical foundation for supporting machine-learning workloads.",
    "reason": "grounding"
  },
  {
    "file": "cover.tex",
    "old_string": "\\lettercontent{I live in Copenhagen and can work in the EU without sponsorship. My earliest start is 1 February 2027, following three months' notice. I seek full-time work of at most 40 hours weekly.}",
    "new_string": "\\lettercontent{I live in Copenhagen and can work in the EU without sponsorship. My earliest start is 1 February 2027, following three months' notice.}",
    "reason": "grounding"
  },
  {
    "file": "cover.tex",
    "old_string": "\\item \\textbf{Scale}: I own a Kafka pipeline processing 40 million shipment events daily into PostgreSQL and BigQuery, orchestrated with Apache Airflow.",
    "new_string": "\\item \\textbf{Scale}: I own a Kafka pipeline that moves 40 million shipment events daily into PostgreSQL and BigQuery, orchestrated with Apache Airflow.",
    "reason": "grounding"
  },
  {
    "file": "cover.tex",
    "old_string": "These are concrete ways I help colleagues use and maintain data products.",
    "new_string": "These are concrete ways I help colleagues use and maintain data products, working closely with the teams that produce and consume them.",
    "reason": "company angle"
  },
  {
    "file": "cover.tex",
    "old_string": "\\lettercontent{I would bring practical experience defining data contracts with five producing teams and building a Looker explore for planners.",
    "new_string": "\\lettercontent{I would bring practical experience defining data contracts with five producing teams so self-service consumers can trust what they query, and building a Looker explore that lets planners answer delivery-delay questions themselves.",
    "reason": "keyword match"
  },
  {
    "file": "cv.tex",
    "old_string": "  \\item \\textbf{Pipelines}: Python, SQL, Apache Airflow, Kafka and dbt.",
    "new_string": "  \\item \\textbf{Pipelines}: Python, SQL, Apache Airflow, Kafka and dbt for batch and streaming ETL/ELT.",
    "reason": "keyword match"
  },
  {
    "file": "cv.tex",
    "old_string": "    \\item Improved monitoring and observability with alerts on 45 Airflow jobs; late feeds were noticed within 15 minutes instead of the next morning.",
    "new_string": "    \\item Added monitoring and observability, with alerts on 45 Airflow jobs, so late feeds were caught within 15 minutes instead of the next morning.",
    "reason": "style"
  },
  {
    "file": "cv.tex",
    "old_string": "    \\item Mentor two junior engineers and lead the weekly data modelling review.",
    "new_string": "    \\item Mentor two junior engineers and lead the weekly data-modelling review.",
    "reason": "style"
  }
]
```

## PART B

**Missed keywords and requirements**
- The posting makes PySpark required and the candidate lacks it. Both documents say so honestly with adjacent Python/Kafka/BigQuery framing. That's the right approach, so I left it alone.
- Covered: SQL, Python, streaming pipelines, orchestration, ETL/ELT, data quality checks, data modelling, cloud data platforms (BigQuery), data contracts, data products, BI/Looker, visualization, CI/CD, DevOps, monitoring, observability, incident support.
- Not mentioned anywhere and not in the profile, so they must stay gaps: distributed data processing (only implied by "40M events/day" scale), AI/ML engineering, LLM/GenAI. The letter should state the AI/ML gap once, plainly, and drop the internal "fit assessment" conversation.
- Not addressed: the posting's prototyping-to-production ask. The profile doesn't support a claim there, so leave it unwritten rather than force it.

**Company and department angles**
- research.md is empty, so I proposed no company claims.
- The strongest honest hook is domain: the candidate already owns shipment-event pipelines in logistics, which maps directly to Maersk's container-logistics domain.
- "Self-service analytics" and "data contracts" are both in the posting and both real in the profile (Looker explore, five producing teams). Together they're the best match to the role's cross-team element.

**Action-oriented reframing**
- Bullets already lead with results (6h to 55m, 70% fewer failed loads, 35% cost cut), consistent with the stated writing style.
- "Improved monitoring and observability" is a vague verb with no clear outcome up front. It now names the action (added alerts) and the result (15 minutes, not the next morning).
- "Own streaming data pipelines" is fine. It doesn't overstate the single pipeline.
- The cover letter needs no changes here. The "Scale / Performance / Reliability" labels read well.

**Tone and style**
- Voice is collaborative and matches the behavioural profile (mentoring, weekly reviews, cross-team contracts). There's no solo-hero or combative framing.
- Cut the letter's paragraph about the "fit assessment". It's internal metadata, it tells a hiring manager about a process failure rather than the candidate, and it reads defensive.
- Cut the "at most 40 hours weekly" sentence. It's a preference/deal-breaker, and volunteering it invites a negative reading.
- The opening "I want to bring end-to-end pipeline ownership to a senior individual-contributor or technical leadership role" hedges across two role types. It's acceptable but could be tightened to one.
- Hyphenation is inconsistent ("data modelling review" in the CV, "data-modelling review" in the profile). Minor, but fixed in the CV for consistency.
