# Interview prep: too_good_to_go__senior_data_engineer, technical

Every claim below is on the paper: the profile, or the CV and letter that were submitted. Nothing here is invented.

## 1. Likely questions
- `q1` (gap, weight 90) How much have you worked with Scala? (the posting requires it; your profile does not back it)
- `q2` (stage, weight 20) Take me through a design decision in your main stack.
- `q3` (stage, weight 20) How do you test and monitor what you ship?

## 2. STAR answers
### Take me through a design decision in your main stack.
- **Situation:** At Tidewater Logistics one design decision stands out: moving the delivery-performance mart onto incremental dbt models. Before that change the mart was rebuilt nightly, a full rebuild that took 6 hours, and the pipeline feeding it - the shipment-events pipeline - carries 40 million events per day from Kafka into a PostgreSQL and BigQuery warehouse, orchestrated with Apache Airflow.
- **Task:** I owned the pipeline end to end, and my task was to make the nightly rebuild of the delivery-performance mart fast enough that the operations team could rely on it.
- **Action:** I rebuilt the mart as incremental dbt models, so each nightly run processed new data and updated the tables it needed to update instead of rebuilding the whole mart from scratch. Running the models through Airflow kept the mart in the same orchestration as the shipment-events pipeline, so a late or failed load was handled in the same place as before. Because I was changing how the most visible mart was produced, I tied the change to the team's data-quality work: I had introduced Great Expectations tests on 30 core tables, and those tests gave us a way to keep trusting the output while the models changed.
- **Result:** The nightly rebuild of the delivery-performance mart went from 6 hours to 55 minutes with the incremental dbt models. The operations team got its delivery-performance numbers during the night instead of waiting on a long rebuild.
- **Lesson:** The lesson for me was to treat the incremental model as a data contract with the team that reads the mart: state what each run refreshes, test it, and let the tests carry the trust while the implementation changes.

### How do you test and monitor what you ship?
- **Situation:** Testing and monitoring have been part of all three of my roles. At Tidewater Logistics the shipment-events pipeline, 40 million events per day from Kafka, is the kind of pipeline where a silent failure shows up as wrong delivery numbers rather than an error message.
- **Task:** My task was to make failures visible and to keep the output of the pipeline trustworthy, both for my own changes and for a new pipeline whose inputs come from other teams.
- **Action:** On quality I introduced Great Expectations tests on 30 core tables, so a bad load was caught by a test rather than discovered downstream. On monitoring I wrote the on-call runbook for the shipment-events pipeline, which meant thinking through what the on-call engineer would see and do when a load was late or broken. On process I moved the team's deployments to GitHub Actions and Terraform, so that a change shipped the same way every time and a new environment takes 20 minutes instead of two days. The same habit goes back to Marlow Retail Group, where I added alerting on the 45 Airflow jobs so late partner feeds are noticed within 15 minutes instead of the next morning, and to my open-source dbt package for freight-rate data tests.
- **Result:** At Tidewater, failed loads dropped 70 percent in the first quarter after the Great Expectations tests went in. At Marlow, a late partner feed stopped waiting until the next morning to be noticed.
- **Lesson:** The lesson was that testing and monitoring are the same job: a test catches bad data early, an alert catches a late job, and both only help if someone has written down what to do when they fire.

### How much have you worked with Scala? (the posting requires it; your profile does not back it)
- **Situation:** I have not worked with Scala. It is listed as not yet in my own skills, and I will not invent a Scala story. What I can talk through in detail is a design decision in my main stack: the shipment-events pipeline at Tidewater Logistics, 40 million events per day from Kafka into a PostgreSQL and BigQuery warehouse, orchestrated with Apache Airflow.
- **Task:** My task as the owner of that pipeline was to make its output something operations could trust, with a data contract with five producing teams, bad loads kept out of the warehouse, and failures visible to whoever was on call.
- **Action:** I introduced Great Expectations tests on 30 core tables, so a bad load failed a test instead of reaching the delivery-performance mart. I defined the data contract with the five producing teams and wrote the on-call runbook for the pipeline, so the on-call engineer knows what to check and what to do when a load is late or wrong. Finally, with five producers landing in shared PostgreSQL and BigQuery tables, I cut the BigQuery bill for the events warehouse by 35 percent by partitioning and clustering the five largest tables, which also gives the data a predictable shape to query.
- **Result:** Failed loads dropped 70 percent in the first quarter after the Great Expectations tests went in, and the BigQuery bill for the events warehouse fell by 35 percent after the partitioning and clustering work.
- **Lesson:** The lesson is that a pipeline design is as much a contract with its producers and consumers as it is a technical choice. That is also how I would take on Scala: read the services I would work with, write tests and small pieces with review from the team, and build one end-to-end exercise before taking on a production component.

Stubs for you to fill in (not written up here):
- Nightly rebuild cut from 6 hours to 55 minutes

## 3. Consistency brief
- "Redshift is the one preferred item I have not used in production." (cover_letter). Likely probe: The posting also names dlt, AWS Kinesis, Glue, Lambda, Metaflow and GitOps, and your profile lists none of them. Is Redshift really the only gap? Prepare: Say plainly that Redshift is one of several stack items you have not used, name the others (dlt, Kinesis, Glue, Lambda, Metaflow, GitOps) and note your AWS experience is on the Google side: BigQuery. Keep the honest framing of the cover letter, but fix the claim that it is the only item.
- "GitOps, CI/CD,
DataOps and stakeholder management are also gaps." (cover_letter). Likely probe: You wrote that CI/CD and stakeholder management are gaps, yet your CV says you moved deployments to GitHub Actions and Terraform and defined a data contract with five producing teams. Which is it? Prepare: Explain what you meant: GitOps as a practised discipline and formal DataOps are the gaps, whereas CI/CD tooling and stakeholder work are things you have done. Give the GitHub Actions and Terraform deployment change and the data contract with five producing teams as your evidence, without calling them gaps.
- "40 million
  events per day from Kafka into a PostgreSQL and BigQuery warehouse with Apache Air-
  flow" (cover_letter). Likely probe: Describe the shipment-events pipeline architecture: what Kafka does, what lands in PostgreSQL and what lands in BigQuery, and how Airflow orchestrates it. Prepare: Have a clear one-minute whiteboard explanation ready that matches the CV wording exactly: Kafka as the source, PostgreSQL and BigQuery as the warehouse sides, Airflow as the orchestrator. Do not add components or services that are not in the CV.
- "can work four days a week at the office" (cover_letter). Likely probe: Days on site matter to you. Is four days in the Copenhagen HQ genuinely acceptable, and how long is your commute? Prepare: Confirm that four days on site fits within your limit of more than 4 days a week on site, and be ready to describe the commute within the one hour each way constraint.
- "After three months' notice, I can start on 1 February 2027." (cover_letter). Likely probe: Why so late a start date, and would anything bring it earlier? Prepare: State that you are employed and serving a three months notice period if you leave, so the earliest start is 2027-02-01. Do not promise an earlier date you cannot back.
- "delivery-performance mart and a Looker explore for operations
  planners" (cv). Likely probe: The embedded role is for Parcels, a direct-to-consumer delivery business. Tell me about the operations people who used your mart and explore, and what decisions they made with them. Prepare: Prepare a short description of the operations planners as users and the delivery-delay questions they used to file as tickets, staying within the CV facts about the Looker explore.

## 4. Honest bridges for the gaps
- **Scala.** I have not used Scala. It is listed as not yet in my own skills, and I will not claim experience I do not have. What I do have is a record of picking up new tools in production. At Marlow Retail Group I built the first Airflow deployment at the company and migrated 45 cron jobs onto it. At Tidewater Logistics I moved the team's deployments to GitHub Actions and Terraform. Most of my pipeline work is in Python, where I cut the nightly rebuild of the delivery-performance mart from 6 hours to 55 minutes with incremental dbt models. (profile: "Built the first Airflow deployment at the company and migrated 45 cron jobs onto it.") I would learn Scala by using it in small, real pieces of the job rather than through a course alone: reading the code of the services I work with, writing tests and small supporting services in Scala with a colleague reviewing them, and building one end-to-end exercise, such as a small streaming job, before taking on a production component.

## 5. Tough questions
### Why are you leaving your current role?
I am leaving because I want a role closer to the product. At Tidewater Logistics I own the shipment-events pipeline end to end, and that is the part of my work I like most: 40 million events per day, the data contract with five producing teams, the on-call runbook. In the Parcels domain at Too Good To Go the senior data engineer is embedded next to product managers and tech leads, so the pipeline is built for the people making delivery decisions rather than further away from them. My goal is a tech-lead role on a data platform team, and I think working in one product domain is a better place to grow into that than a role that stays purely central. It is also a move toward a mission I care about, reducing food waste.

### You lack a skill we ask for. How will you manage?
Scala is the one part of the stack I have not done, and I will not pretend otherwise. The more important pattern is familiar to me, though: at Marlow Retail Group I built the first Airflow deployment at the company and migrated 45 cron jobs onto it, and at Tidewater Logistics I moved the team's deployments to GitHub Actions and Terraform. Picking up a new tool inside a real job is how I have learned most of what I know. For Scala I would start by reading the services I would work with, write tests and small pieces in Scala with review from the team, and build one end-to-end exercise before taking on a production component.

### Where do you see yourself in five years?
Within the next few years I want to be a tech lead on a data platform team, working close to the product. My career goal is tech lead within 3 years. The way there is through the work I already do: owning a pipeline end to end, leading the weekly data-modelling review at Tidewater Logistics, mentoring two junior engineers, and having done the hiring side, running the interviews for three data-engineering hires and writing the team's take-home exercise. Embedded in the Parcels domain, I would want to become the person the product managers and tech leads rely on for the data pipelines behind logistics and inventory, and the primary link to the central data platform, while deepening my skills on the parts of the stack I have not used yet.

### What is your biggest weakness?
A weakness I have noticed is that I can go deep into the pipeline before checking in with the people who will read the output. Building the Looker explore for the operations team helped me with that: it forced me to see what planners actually needed to answer delivery-delay questions themselves instead of filing tickets. I have made checking in earlier a habit since. The other honest gap is my self-assessment in the cover letter. I wrote that CI/CD was a gap, but I moved the team's deployments to GitHub Actions and Terraform, so I should have been clearer that what I meant was GitOps as a practised discipline and formal DataOps, not the tooling.

No verified company claim was available, so 'why this company' is left for you to research.

## 6. Questions to ask
- (role) What does the Parcels data landscape look like today, and which pipelines would I own first?
- (tech and growth) You were not explicit about which technologies are required versus preferred. How much of the Parcels work would be in Scala, and how much in Python?
- (role) What would success look like for this role in the first year from the Parcels product side?
- (tech and growth) What does the path toward tech lead look like on the data platform side?
- (tech and growth) How does the team work with AWS tooling such as dlt, Kinesis, Glue and Metaflow, and is support for learning them part of the role?
- (team) How does the boundary work between the embedded Parcels engineer and the central Data Platform team, especially when a Parcels need conflicts with platform standards?

## 7. Research (verified, public, professional only)
- none supplied

## 8. Logistics
- Date: unknown
- Interviewers: unknown
- Format: video
- Check camera, sound and light ten minutes early.
- Have a glass of water within reach.
- Clarify a vague question before answering; a five-second pause is fine.
- Close by asking whether they want anything else from you.
