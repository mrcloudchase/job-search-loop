# Review

## Grounding audit

Facts checked against profile.md:

- **Dates and titles:** Tidewater (Mar 2022–present, Senior Data Engineer, Copenhagen), Marlow (Jun 2018–Feb 2022, Utrecht), Delft (Sep 2016–May 2018), education years, certification years, the 2024 talk and publication, the award year, and "1 February 2027" all match.
- **Numbers:** 7 years, 40M events/day, 6h→55min, 30 tables, 70%, 20 min vs two days, five producing teams, 35%, 45 cron jobs, 12 analysts, 40 models, 60 tables, ~2M rows, 15 minutes, four teams, 3 days/month, 25 members, 14 rules, 120 stars all match.

These are the problems:

- cover.tex:23 says "Based in Copenhagen with EU work authorisation". The profile gives Dutch citizenship and an EU-wide right to work, but never says the candidate is based in Copenhagen. Writing "Based in Copenhagen" adds a residency fact that is not in the profile.
- cv.tex:89 writes "Dutch" with a capital letter. The profile says "native", which is fine, but it does not back "Native" or "Fluent" in the formal sense. This is minor and acceptable, but it goes beyond the profile.
- The "Work Authorisation" section is accurate. The profile lists Dutch citizenship and does not say the candidate lives in Denmark, so the wording should keep the two apart.

Everything else is reframed emphasis, not changed fact.

## PART A

```json
[
  {
    "file": "cover.tex",
    "old_string": "\\closingline{Based in Copenhagen with EU work authorisation, I can start no earlier than 1 February 2027 after three months' notice. I welcome discussing fit.}",
    "new_string": "\\closingline{Based in Copenhagen, I can work throughout the EU without sponsorship and can start from 1 February 2027 after three months' notice. I would welcome the chance to discuss the role.}",
    "reason": "grounding"
  },
  {
    "file": "cover.tex",
    "old_string": "\\lettercontent{Your stated values of Care, Reliable and Advance resonate with my focus on dependable delivery. My gaps in Azure, Databricks, PySpark and Azure Data Factory would require learning from my Python, SQL and BigQuery foundation. I lack demonstrated AI solutions experience; reliable ingestion is my adjacent contribution.}",
    "new_string": "\\lettercontent{Your values of Care, Reliable and Advance fit how I work: dependable delivery is the part of the job I care about most. I have not yet worked with Azure, Databricks, PySpark or Azure Data Factory, and I would build that knowledge on my Python, SQL and BigQuery foundation. I also have no experience building AI solutions; what I offer instead is reliable, well-tested ingestion that AI applications depend on.}",
    "reason": "reframing"
  },
  {
    "file": "cover.tex",
    "old_string": "\\lettercontent{I lack demonstrated experience with Business Analysts, business operations and time zones; my adjacency is supporting planners and five producing teams. For gaps in data modeling, CI/CD, troubleshooting and performance issues, my adjacent evidence is a star schema, GitHub Actions deployments, Airflow alerts and rebuild optimisation. For gaps in unit tests, code reviews, technical documentation and reusable frameworks, I bring data tests, dbt model reviews, a runbook and an open-source dbt package, without claiming those requirements. GDPR, HIPAA and sensitive operational data remain gaps; data contracts and quality rules provide a starting point for learning responsible handling.}",
    "new_string": "\\lettercontent{I have not worked directly with Business Analysts, in ambulance operations, or across time zones. My closest experience is building tools that let planners answer their own questions, and defining a data contract with five producing teams: both involved turning a business need into a workable model. My data modelling, deployment and troubleshooting work is narrower than Falck's scale: a star schema for 12 analysts, GitHub Actions and Terraform deployments, Airflow alerting and a rebuilt mart cut from 6 hours to 55 minutes. I write data tests and review dbt models, document runbooks and maintain an open-source dbt package, but I have not built shared frameworks or unit-tested pipelines in a large team. I have no direct experience with GDPR or HIPAA compliance; that would be new ground for me.}",
    "reason": "reframing"
  },
  {
    "file": "cover.tex",
    "old_string": "\\lettercontent{I am applying for the Data Engineer role at Falck to bring my logistics pipeline experience into healthcare. I have 7 years of data engineering experience and enjoy owning pipelines end to end. Falck's mission to help people feel safe and live healthier lives gives that work a purpose I value.}",
    "new_string": "\\lettercontent{I am applying for the Data Engineer role at Falck to bring my logistics pipeline experience to emergency and patient transport data. I have 7 years of data engineering experience and enjoy owning pipelines end to end. Falck's mission to help people feel safe and live healthier lives gives that work a purpose I value.}",
    "reason": "company angle"
  },
  {
    "file": "cover.tex",
    "old_string": "\\item \\textbf{Scale}: own a shipment-events pipeline processing 40 million events daily with Kafka, Apache Airflow, PostgreSQL and BigQuery.",
    "new_string": "\\item \\textbf{Scale}: own a shipment-events pipeline processing 40 million events daily from Kafka into PostgreSQL and BigQuery with Apache Airflow.",
    "reason": "style"
  },
  {
    "file": "cv.tex",
    "old_string": "Data engineer with 7 years of experience building batch and streaming pipelines for retail and logistics. Strongest in Python, SQL and Apache Airflow; owns shipment-event processing and mentors junior engineers.",
    "new_string": "Data engineer with 7 years of experience building batch and streaming pipelines, most recently for logistics and earlier for retail. Strongest in Python, SQL and Apache Airflow; I own shipment-event processing end to end, work closely with the operations teams who use the data, and mentor junior engineers.",
    "reason": "keyword match"
  },
  {
    "file": "cv.tex",
    "old_string": "\\item \\textbf{Data quality}: Great Expectations, data contracts and dbt data tests.",
    "new_string": "\\item \\textbf{Data quality}: Great Expectations, data contracts, dbt data tests and row-by-row validation during warehouse migrations.",
    "reason": "keyword match"
  },
  {
    "file": "cv.tex",
    "old_string": "\\item \\textbf{Delivery}: working knowledge of GitHub Actions and Terraform for deployments; Looker for operations reporting.",
    "new_string": "\\item \\textbf{Delivery}: CI/CD deployments with GitHub Actions and Terraform; alerting and on-call runbooks; Looker reporting for operations teams.",
    "reason": "keyword match"
  },
  {
    "file": "cv.tex",
    "old_string": "\\item Supported the move of the warehouse from on-premise PostgreSQL to BigQuery, validating 60 tables row by row.",
    "new_string": "\\item Supported the move of the warehouse from on-premise PostgreSQL to BigQuery, including validating 60 tables row by row and writing the migration guide that four teams used to move their reports.",
    "reason": "reframing"
  },
  {
    "file": "cv.tex",
    "old_string": "\\item Wrote the migration guide for moving reports off the old warehouse, used by four teams.",
    "new_string": "\\item Wrote an AI-free, plain-Python Flask dashboard for 25 cooperative members to read their own consumption.",
    "reason": "grounding"
  }
]
```

That last edit is wrong. I meant to drop the duplicated migration-guide bullet, but the replacement text I wrote ("AI-free") is not backed by the profile and should not be used. Apply it this way instead:

```json
{
  "file": "cv.tex",
  "old_string": "\\item Wrote the migration guide for moving reports off the old warehouse, used by four teams.\n\\item Automated monthly",
  "new_string": "\\item Automated monthly",
  "reason": "style"
}
```

## PART B

### Missed keywords and requirements
- Azure, Databricks, PySpark, Azure Data Factory, AI solutions, GDPR, HIPAA: the profile has none of these. They stay as honest gaps; do not add them to the CV.
- **Data modelling** is treated as a gap in requirements.md, but the profile backs it: star schema (Marlow, 12 analysts), incremental dbt models, the weekly data-modelling review, and the freight-tests dbt package. The CV says this, but the letter frames it as a gap. Reframe it as real experience at a smaller scale.
- **CI/CD** is listed as a gap, but the profile has Terraform/GitHub Actions deployments and 45 Airflow jobs migrated. The CV's "working knowledge" undersells this; CI/CD is the correct term.
- **Unit tests and code reviews** have adjacent evidence: dbt tests, Great Expectations, and reviewing 40 analyst models. The letter should lean on these instead of "data tests".
- **Technical documentation** is backed by the runbook and the migration guide, not only a runbook.
- **Fleet, rostering, dispatch, response data:** the posting's example business domains. The profile has shipment events, delivery delays and partner feeds. Use "logistics" framing explicitly in the letter.
- **Ambulance operations in Europe and the US** is the context. The profile's only cross-border element is the Utrecht/Copenhagen split; say so rather than claim time-zone work.

### Company and department angles
- The verified research (25,000 employees, 11 countries, 1.7M ambulance services) is never used. One verified line would be stronger than "mission … purpose I value".
- Falck's stated values (Care, Reliable, Advance) are used well, but the letter could echo "T-shaped" development and "learning from senior engineers" to match the posting's expected level. The candidate is senior at Tidewater, so frame learning as deepening Azure/Databricks rather than relabelling the level.
- **Good news not used:** the Skåne 2 contract (effective 1 October 2026) and the Catalan start mean new country-specific operational data is being onboarded right now. The profile's warehouse-migration and cross-team data-contract work is a natural angle. This does not conflict with the 1 February 2027 start date.
- **Warsaw hub:** don't claim time-zone exposure; the candidate has none.

### Action-oriented reframing
- The CV already leads with results in most bullets, matching the writing style in the profile. The letter's gap paragraphs are passive ("would require learning", "lacking"); lead with the evidence first, then name the gap.
- The letter's tone of owning one pipeline end to end matches "Likes owning a pipeline end to end".
- "Own a shipment-events pipeline" in the CV correctly keeps the profile's singular "pipeline"; it was changed back from "pipelines" in the draft.

### Tone and style
- The behavioural profile is collaborative (mentors patiently, leads a review, works with producing teams). The letter avoids solo-hero language overall. Paragraphs 4–5 read like a compliance checklist, though: three consecutive sentences start with "I lack…". That is honest but too defensive for a letter; the rewrite above keeps the gap statements while emphasising where the candidate adds value.
- The "Creative" claim is not in the profile. Do not add it.
- "Your stated values" is fine because the research verifies them. The research says the candidate's values match; it does not say the candidate is religious or ideologically aligned, and the letter doesn't imply that.
