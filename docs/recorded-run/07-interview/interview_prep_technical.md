# Interview prep: too_good_to_go__senior_data_engineer, technical

Every claim below is on the paper: the profile, or the CV and letter that were submitted. Nothing here is invented.

## 1. Likely questions
- `q1` (gap, weight 90) How much have you worked with Scala? (the posting requires it; your profile does not back it)
- `q2` (stage, weight 20) Take me through a design decision in your main stack.
- `q3` (stage, weight 20) How do you test and monitor what you ship?

## 2. STAR answers
### Take me through a design decision in your main stack.
- **Situation:** At Tidewater Logistics I own the shipment-events pipeline: 40 million events per day from Kafka into a PostgreSQL and BigQuery warehouse, orchestrated with Apache Airflow. The nightly rebuild of the delivery-performance mart took 6 hours.
- **Task:** I wanted that rebuild to stop consuming the whole night and to make the cost of the events warehouse visible and controllable.
- **Action:** I replaced the rebuild with incremental dbt models, so each night folds in new events instead of recomputing the mart from scratch. For the warehouse, I partitioned and clustered the five largest tables, so queries read the slices they need instead of the full table.
- **Result:** The rebuild fell from 6 hours to 55 minutes, and the BigQuery bill for the events warehouse dropped by 35 percent. Both changes also made the data contract work easier, because incremental models depend on the events arriving in a predictable shape, which is exactly what the contract I agreed with the producing teams guarantees.
- **Lesson:** The lesson is that modelling choices and bill are the same decision: incremental dbt models gave me the speed, and partitioning and clustering gave me the cost, and both are only safe if the upstream data keeps its contract.

### How do you test and monitor what you ship?
- **Situation:** At Marlow Retail Group I built the first Airflow deployment at the company and migrated 45 cron jobs onto it. My Python ingestion services loaded partner price feeds over REST and SFTP, handling about 2 million rows a day. Late partner feeds were only noticed the next morning.
- **Task:** I wanted a late feed to be caught while the team was still awake, and I wanted data-quality problems to stop bad loads from landing.
- **Action:** I added alerting on the 45 Airflow jobs so a late feed raised a signal right away instead of being discovered in the morning. Later, at Tidewater Logistics, I introduced data-quality tests with Great Expectations on 30 core tables, next to the pipeline rather than after it, and I wrote the on-call runbook so whoever is on duty knows what to check first.
- **Result:** At Marlow, late partner feeds were noticed within 15 minutes instead of the next morning. At Tidewater, failed loads dropped 70 percent in the first quarter.
- **Lesson:** The lesson is that monitoring and testing are two halves of the same job: alerting tells you something is late, and quality tests tell you whether what arrived is usable, so I now build both into a pipeline from the start.

Stubs for you to fill in (not written up here):
- Nightly rebuild cut from 6 hours to 55 minutes

## 3. Consistency brief
- "Redshift is the one preferred item I have not used in production." (cover_letter). Likely probe: What tells you that git history, tickets and the new environment pipeline all show work beyond the CV, and whom could we call to verify? Prepare: Have a specific example ready of a change you made that the CV does not describe, and make clear which parts of this sentence go beyond the CV's evidence.
- "GitOps, CI/CD,
DataOps and stakeholder management are also gaps." (cover_letter). Likely probe: What is your experience with CI/CD and building deployments through pipelines? Prepare: After this brief, do not say CI/CD is a gap. Prepare concrete detail on GitHub Actions and Terraform deployment work, and frame GitOps, DataOps and stakeholder management as partially developed rather than absent.
- "I am based in Copenhagen, the city of the role" (cover_letter). Likely probe: Which part of Copenhagen do you live in, and is your commute comfortable? Prepare: Have a short, concrete answer about your location and commute lined up.
- "• Owns the shipment-events pipeline: 40 million events per day from Kafka into a PostgreSQL and BigQuery
  warehouse, orchestrated with Apache Airflow" (cv). Likely probe: Walk me through the architecture: where does Kafka hand off to Airflow, and what happens to an event from arrival to the warehouse? Prepare: Be ready to go step by step through ingestion, orchestration, landing and the warehouse layout. The CV repeats this bullet in several places, so the interviewer will likely probe the detail.

## 4. Honest bridges for the gaps
- **Scala.** I have not worked in Scala. It is on my own list of things I have not learned yet, and I would not present any Scala experience. My pipeline work is in Python, SQL and Apache Airflow, with Kafka feeding a PostgreSQL and BigQuery warehouse, and I own streaming and batch work daily on the shipment-events pipeline. (profile: "Strongest in Python, SQL and Apache Airflow.") I would start by reading the Scala code in the job's own pipelines, pair with an engineer on small changes, and work through a structured Scala course for the functional concepts, checking my progress with code review before taking on anything production-critical.

## 5. Tough questions
### Why are you leaving your current role?
I have built and owned the shipment-events pipeline at Tidewater Logistics for the last few years, from the data contract with the producing teams through the on-call runbook. What I want next is more time working directly with the people who use the data. I am being honest about that. At Tidewater my closest partners are other engineers; I built a Looker explore so operations planners could answer delivery-delay questions themselves, but that was a side of the job rather than the centre of it. The Parcels role puts the data engineer beside product managers and tech leads for one domain, and that is the part of the work I enjoy most. It is a move towards the work, not away from Tidewater.

### You lack a skill we ask for. How will you manage?
There are a few. I have not worked in Scala, and I have not run AWS Redshift in production. The posting also lists GitOps and DataOps, which I have not practised as named disciplines. I would rather be clear about that than overstate it. What I do bring is close: I work in Python and SQL and orchestrate with Apache Airflow every day; I supported the move from on-premise PostgreSQL to BigQuery and cut the BigQuery bill by partitioning and clustering; and I moved my team's deployments to GitHub Actions and Terraform. Learning engines and platforms is something I have done before, and I am comfortable asking for review while I get up to speed. If Scala is the main language of the domain, I would want to know how much of the day-to-day work depends on it.

### Where do you see yourself in five years?
My career goal is to be a tech lead, and I see the next few years as building towards that. I already mentor two junior engineers and lead the weekly data-modelling review, so the lead path is one I am walking rather than waiting for. In three to five years I would like to be the person a domain trusts when it needs a data platform decision made and defended. I am cautious about predicting too far ahead; what I am certain of is that I want to keep owning pipelines end to end and to work closer to the product, rather than moving towards pure management. I say that knowing the company's direction is ambitious; I would rather grow with it than guess which exact role exists later.

### What is your biggest weakness?
I am slow to defend my own time when someone needs something quickly. I like owning things end to end, and that makes it easy to take on a request directly rather than route it through a process. The clearest case is the Looker explore I built for the operations team, so that planners could answer delivery-delay questions themselves instead of filing tickets. That worked because the problem was persistent and the tool fitted it; I have not always been as disciplined about recognising when a request is a one-off that does not deserve a pipeline. I am working on asking whether a request needs a durable solution before I start; the data contract work taught me the same habit from the other side, agreeing expectations up front with five producing teams rather than absorbing every change myself.

No verified company claim was available, so 'why this company' is left for you to research.

## 6. Questions to ask
- (role) What does the Parcels data platform look like today, and what are the first problems you would want this role to solve?
- (role) How does the Parcels team work day to day with the central Data Platform team when standards and the domain's needs pull in different directions?
- (tech and growth) Which parts of the Parcels data work are real-time streaming and which are batch, and why the split?
- (role) What would good look like for this role after the first six months?
- (tech and growth) What data-quality and SLA expectations does Parcels have towards product and logistics, and who sets them?
- (tech and growth) How does the team make room for learning new tools for someone joining from a BigQuery background?

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
