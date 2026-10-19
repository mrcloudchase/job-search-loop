# Interview prep: A.P. Moller - Maersk — Senior Data Engineer, technical

Every claim below is on the paper: the profile, or the CV and letter that were submitted. Nothing here is invented.

## 1. Likely questions
- `q1` (gap, weight 90) How much have you worked with PySpark? (the posting requires it; your profile does not back it)
- `q2` (gap, weight 90) How much have you worked with distributed data processing? (the posting requires it; your profile does not back it)
- `q3` (gap, weight 90) How much have you worked with data engineering experience? (the posting requires it; your profile does not back it)
- `q4` (gap, weight 70) How much have you worked with AI/ML engineering? (the posting prefers it; your profile does not back it)
- `q5` (gap, weight 70) How much have you worked with LLM-based applications? (the posting prefers it; your profile does not back it)
- `q6` (requirement, weight 40) Give an example of using SQL in production.
- `q7` (requirement, weight 40) Give an example of using Python in production.
- `q8` (requirement, weight 40) Give an example of using streaming data pipelines in production.
- `q9` (requirement, weight 40) Give an example of using orchestration in production.
- `q10` (requirement, weight 40) Give an example of using ETL/ELT pipelines in production.
- `q11` (requirement, weight 40) Give an example of using data quality checks in production.
- `q12` (requirement, weight 40) Give an example of using data modelling in production.
- `q13` (requirement, weight 40) Give an example of using cloud data platforms in production.
- `q14` (requirement, weight 40) Give an example of using data contracts in production.
- `q15` (requirement, weight 40) Give an example of using data products in production.
- `q16` (requirement, weight 40) Give an example of using analytics/BI consumption patterns in production.
- `q17` (requirement, weight 40) Give an example of using CI/CD in production.
- `q18` (requirement, weight 40) Give an example of using DevOps in production.
- `q19` (requirement, weight 40) Give an example of using monitoring in production.
- `q20` (requirement, weight 40) Give an example of using incident support in production.
- `q21` (requirement, weight 40) Give an example of using observability in production.
- `q22` (requirement, weight 30) Give an example of using visualization in production.
- `q23` (requirement, weight 30) Give an example of using machine learning in production.
- `q24` (stage, weight 20) Take me through a design decision in SQL, Python, PySpark, distributed data processing.
- `q25` (stage, weight 20) How do you test and monitor what you ship?

## 2. STAR answers
### How much have you worked with PySpark? (the posting requires it; your profile does not back it)
- **Situation:** This is an adjacent example only; it does not establish experience in the capability being asked about. At Marlow Retail Group, partner price feeds arrived through REST and SFTP. The ingestion services handled about 2 million rows a day.
- **Task:** My work was to build ingestion services for those partner price feeds.
- **Action:** I wrote the ingestion services in Python for both REST and SFTP feeds. Python is also among my strongest skills, alongside SQL and Apache Airflow.
- **Result:** The services handled about 2 million rows a day.
- **Lesson:** The lesson I would draw is to explain ingestion work through its source interfaces and the volume it handles.

### How much have you worked with distributed data processing? (the posting requires it; your profile does not back it)
- **Situation:** This is an adjacent example only; it does not establish experience in the capability being asked about. At Marlow Retail Group, partner price feeds arrived through REST and SFTP. The ingestion services handled about 2 million rows a day.
- **Task:** My work was to build ingestion services for those partner price feeds.
- **Action:** I wrote the ingestion services in Python for both REST and SFTP feeds. Python is also among my strongest skills, alongside SQL and Apache Airflow.
- **Result:** The services handled about 2 million rows a day.
- **Lesson:** The lesson I would draw is to explain ingestion work through its source interfaces and the volume it handles.

### How much have you worked with data engineering experience? (the posting requires it; your profile does not back it)
- **Situation:** The supplied gap label conflicts with my profile, which documents data engineering experience. At Tidewater Logistics, I own the shipment-events pipeline. It handles 40 million events per day from Kafka into a PostgreSQL and BigQuery warehouse.
- **Task:** My responsibility is ownership of that shipment-events pipeline.
- **Action:** The pipeline is orchestrated with Apache Airflow. I also defined the shipment-events data contract with five producing teams and wrote the on-call runbook for the pipeline.
- **Result:** The pipeline handles 40 million events per day into the warehouse, with a defined data contract and an on-call runbook.
- **Lesson:** The lesson I would draw is that pipeline ownership includes producer agreements and operational documentation.

### How much have you worked with AI/ML engineering? (the posting prefers it; your profile does not back it)
- **Situation:** This is an adjacent example only; it does not establish experience in the capability being asked about. At Tidewater Logistics, my work includes warehouse data quality and ownership of the shipment-events pipeline.
- **Task:** My data-quality work covered 30 core tables. My operational work also included the pipeline's on-call runbook.
- **Action:** I introduced data-quality tests with Great Expectations on the core tables and wrote the on-call runbook. My separate monitoring example is adding alerting on the Airflow jobs at Marlow Retail Group.
- **Result:** Failed loads dropped 70 percent in the first quarter after the data-quality tests were introduced.
- **Lesson:** The lesson I would draw is to discuss testing, alerting and operational documentation as concrete parts of pipeline ownership.

### How much have you worked with LLM-based applications? (the posting prefers it; your profile does not back it)
- **Situation:** This is an adjacent example only; it does not establish experience in the capability being asked about. At Marlow Retail Group, partner price feeds arrived through REST and SFTP. The ingestion services handled about 2 million rows a day.
- **Task:** My work was to build ingestion services for those partner price feeds.
- **Action:** I wrote the ingestion services in Python for both REST and SFTP feeds. Python is also among my strongest skills, alongside SQL and Apache Airflow.
- **Result:** The services handled about 2 million rows a day.
- **Lesson:** The lesson I would draw is to explain ingestion work through its source interfaces and the volume it handles.

### Give an example of using SQL in production.
- **Situation:** At Marlow Retail Group, I worked on customer-orders data in PostgreSQL. The customer-orders star schema was used by 12 analysts.
- **Task:** My work was to design the customer-orders star schema for those analysts.
- **Action:** I designed the schema in PostgreSQL. This is my supported example for discussing SQL and data modelling; my profile also lists SQL and PostgreSQL among my strongest skills.
- **Result:** The customer-orders star schema was used by 12 analysts.
- **Lesson:** The lesson I would draw is to connect data modelling work to the analysts who use the resulting schema.

### Give an example of using Python in production.
- **Situation:** At Marlow Retail Group, partner price feeds arrived through REST and SFTP. The ingestion services handled about 2 million rows a day.
- **Task:** My work was to build ingestion services for those partner price feeds.
- **Action:** I wrote the ingestion services in Python for both REST and SFTP feeds. Python is also among my strongest skills, alongside SQL and Apache Airflow.
- **Result:** The services handled about 2 million rows a day.
- **Lesson:** The lesson I would draw is to explain ingestion work through its source interfaces and the volume it handles.

### Give an example of using streaming data pipelines in production.
- **Situation:** At Tidewater Logistics, I own the shipment-events pipeline. It handles 40 million events per day from Kafka into a PostgreSQL and BigQuery warehouse.
- **Task:** My responsibility is ownership of that shipment-events pipeline.
- **Action:** The pipeline is orchestrated with Apache Airflow. I also defined the shipment-events data contract with five producing teams and wrote the on-call runbook for the pipeline.
- **Result:** The pipeline handles 40 million events per day into the warehouse, with a defined data contract and an on-call runbook.
- **Lesson:** The lesson I would draw is that pipeline ownership includes producer agreements and operational documentation.

### Give an example of using orchestration in production.
- **Situation:** At Marlow Retail Group, I worked with 45 cron jobs and partner feeds. Late partner feeds had been noticed the next morning.
- **Task:** My work covered introducing Airflow orchestration and adding alerts for late partner feeds.
- **Action:** I built the company's first Airflow deployment and migrated 45 cron jobs onto it. I added alerting on the 45 Airflow jobs.
- **Result:** Late partner feeds were noticed within 15 minutes instead of the next morning.
- **Lesson:** The lesson I would draw is to connect orchestration with alerting so late inputs become visible sooner.

### Give an example of using ETL/ELT pipelines in production.
- **Situation:** At Marlow Retail Group, partner price feeds arrived through REST and SFTP. The ingestion services handled about 2 million rows a day.
- **Task:** My work was to build ingestion services for those partner price feeds.
- **Action:** I wrote the ingestion services in Python for both REST and SFTP feeds. Python is also among my strongest skills, alongside SQL and Apache Airflow.
- **Result:** The services handled about 2 million rows a day.
- **Lesson:** The lesson I would draw is to explain ingestion work through its source interfaces and the volume it handles.

### Give an example of using data quality checks in production.
- **Situation:** At Tidewater Logistics, my work includes warehouse data quality and ownership of the shipment-events pipeline.
- **Task:** My data-quality work covered 30 core tables. My operational work also included the pipeline's on-call runbook.
- **Action:** I introduced data-quality tests with Great Expectations on the core tables and wrote the on-call runbook. My separate monitoring example is adding alerting on the Airflow jobs at Marlow Retail Group.
- **Result:** Failed loads dropped 70 percent in the first quarter after the data-quality tests were introduced.
- **Lesson:** The lesson I would draw is to discuss testing, alerting and operational documentation as concrete parts of pipeline ownership.

### Give an example of using data modelling in production.
- **Situation:** At Marlow Retail Group, I worked on customer-orders data in PostgreSQL. The customer-orders star schema was used by 12 analysts.
- **Task:** My work was to design the customer-orders star schema for those analysts.
- **Action:** I designed the schema in PostgreSQL. This is my supported example for discussing SQL and data modelling; my profile also lists SQL and PostgreSQL among my strongest skills.
- **Result:** The customer-orders star schema was used by 12 analysts.
- **Lesson:** The lesson I would draw is to connect data modelling work to the analysts who use the resulting schema.

### Give an example of using cloud data platforms in production.
- **Situation:** At Tidewater Logistics, the shipment-events pipeline feeds a PostgreSQL and BigQuery warehouse. My cloud-platform work included the events warehouse's BigQuery bill.
- **Task:** My optimization work focused on the five largest tables in the events warehouse.
- **Action:** I partitioned and clustered those tables. This is a concrete example of my work optimizing BigQuery, which is among my strongest skills.
- **Result:** The BigQuery bill for the events warehouse fell by 35 percent.
- **Lesson:** The lesson I would draw is to connect warehouse optimization to a measurable operating outcome.

### Give an example of using data contracts in production.
- **Situation:** At Tidewater Logistics, I own the shipment-events pipeline, which handles 40 million events per day. The pipeline has five producing teams.
- **Task:** My work included defining the shipment-events data contract and writing the pipeline's on-call runbook.
- **Action:** I defined the data contract with the producing teams and wrote the runbook. These are the documented examples I can discuss for producer coordination and preparation for operational support.
- **Result:** The shipment-events pipeline has a defined data contract and an on-call runbook.
- **Lesson:** The lesson I would draw is that operating a pipeline includes documenting its producer agreement and its support procedures.

### Give an example of using data products in production.
- **Situation:** At Tidewater Logistics, operations planners had delivery-delay questions and filed tickets to get answers.
- **Task:** My work was to build a Looker explore for the operations team so planners could answer those questions themselves.
- **Action:** I built the Looker explore for the operations team. This is my documented example of enabling reporting consumption and self-service access for a specific group of users.
- **Result:** Planners can answer delivery-delay questions themselves instead of filing tickets.
- **Lesson:** The lesson I would draw is to explain a data product through the questions its users can answer independently.

### Give an example of using analytics/BI consumption patterns in production.
- **Situation:** At Tidewater Logistics, operations planners had delivery-delay questions and filed tickets to get answers.
- **Task:** My work was to build a Looker explore for the operations team so planners could answer those questions themselves.
- **Action:** I built the Looker explore for the operations team. This is my documented example of enabling reporting consumption and self-service access for a specific group of users.
- **Result:** Planners can answer delivery-delay questions themselves instead of filing tickets.
- **Lesson:** The lesson I would draw is to explain a data product through the questions its users can answer independently.

### Give an example of using CI/CD in production.
- **Situation:** At Tidewater Logistics, creating a new environment previously took two days. My work included changing the team's deployments.
- **Task:** My deployment work covered moving the team to GitHub Actions and Terraform.
- **Action:** I moved the team's deployments to GitHub Actions and Terraform. My profile describes my knowledge of both tools as working knowledge, which is how I would describe my depth in an interview.
- **Result:** A new environment takes 20 minutes instead of two days.
- **Lesson:** The lesson I would draw is to explain deployment automation through its effect on environment setup.

### Give an example of using DevOps in production.
- **Situation:** At Tidewater Logistics, creating a new environment previously took two days. My work included changing the team's deployments.
- **Task:** My deployment work covered moving the team to GitHub Actions and Terraform.
- **Action:** I moved the team's deployments to GitHub Actions and Terraform. My profile describes my knowledge of both tools as working knowledge, which is how I would describe my depth in an interview.
- **Result:** A new environment takes 20 minutes instead of two days.
- **Lesson:** The lesson I would draw is to explain deployment automation through its effect on environment setup.

### Give an example of using monitoring in production.
- **Situation:** At Marlow Retail Group, I worked with 45 cron jobs and partner feeds. Late partner feeds had been noticed the next morning.
- **Task:** My work covered introducing Airflow orchestration and adding alerts for late partner feeds.
- **Action:** I built the company's first Airflow deployment and migrated 45 cron jobs onto it. I added alerting on the 45 Airflow jobs.
- **Result:** Late partner feeds were noticed within 15 minutes instead of the next morning.
- **Lesson:** The lesson I would draw is to connect orchestration with alerting so late inputs become visible sooner.

### Give an example of using incident support in production.
- **Situation:** At Tidewater Logistics, I own the shipment-events pipeline, which handles 40 million events per day. The pipeline has five producing teams.
- **Task:** My work included defining the shipment-events data contract and writing the pipeline's on-call runbook.
- **Action:** I defined the data contract with the producing teams and wrote the runbook. These are the documented examples I can discuss for producer coordination and preparation for operational support.
- **Result:** The shipment-events pipeline has a defined data contract and an on-call runbook.
- **Lesson:** The lesson I would draw is that operating a pipeline includes documenting its producer agreement and its support procedures.

### Give an example of using observability in production.
- **Situation:** At Marlow Retail Group, I worked with 45 cron jobs and partner feeds. Late partner feeds had been noticed the next morning.
- **Task:** My work covered introducing Airflow orchestration and adding alerts for late partner feeds.
- **Action:** I built the company's first Airflow deployment and migrated 45 cron jobs onto it. I added alerting on the 45 Airflow jobs.
- **Result:** Late partner feeds were noticed within 15 minutes instead of the next morning.
- **Lesson:** The lesson I would draw is to connect orchestration with alerting so late inputs become visible sooner.

### Give an example of using visualization in production.
- **Situation:** At Tidewater Logistics, operations planners had delivery-delay questions and filed tickets to get answers.
- **Task:** My work was to build a Looker explore for the operations team so planners could answer those questions themselves.
- **Action:** I built the Looker explore for the operations team. This is my documented example of enabling reporting consumption and self-service access for a specific group of users.
- **Result:** Planners can answer delivery-delay questions themselves instead of filing tickets.
- **Lesson:** The lesson I would draw is to explain a data product through the questions its users can answer independently.

### Give an example of using machine learning in production.
- **Situation:** This is an adjacent example only; it does not establish experience in the capability being asked about. At Tidewater Logistics, my work includes warehouse data quality and ownership of the shipment-events pipeline.
- **Task:** My data-quality work covered 30 core tables. My operational work also included the pipeline's on-call runbook.
- **Action:** I introduced data-quality tests with Great Expectations on the core tables and wrote the on-call runbook. My separate monitoring example is adding alerting on the Airflow jobs at Marlow Retail Group.
- **Result:** Failed loads dropped 70 percent in the first quarter after the data-quality tests were introduced.
- **Lesson:** The lesson I would draw is to discuss testing, alerting and operational documentation as concrete parts of pipeline ownership.

### Take me through a design decision in SQL, Python, PySpark, distributed data processing.
- **Situation:** At Marlow Retail Group, I worked on customer-orders data in PostgreSQL. The customer-orders star schema was used by 12 analysts.
- **Task:** My work was to design the customer-orders star schema for those analysts.
- **Action:** I designed the schema in PostgreSQL. This is my supported example for discussing SQL and data modelling; my profile also lists SQL and PostgreSQL among my strongest skills.
- **Result:** The customer-orders star schema was used by 12 analysts.
- **Lesson:** The lesson I would draw is to connect data modelling work to the analysts who use the resulting schema.

### How do you test and monitor what you ship?
- **Situation:** At Tidewater Logistics, my work includes warehouse data quality and ownership of the shipment-events pipeline.
- **Task:** My data-quality work covered 30 core tables. My operational work also included the pipeline's on-call runbook.
- **Action:** I introduced data-quality tests with Great Expectations on the core tables and wrote the on-call runbook. My separate monitoring example is adding alerting on the Airflow jobs at Marlow Retail Group.
- **Result:** Failed loads dropped 70 percent in the first quarter after the data-quality tests were introduced.
- **Lesson:** The lesson I would draw is to discuss testing, alerting and operational documentation as concrete parts of pipeline ownership.

Stubs for you to fill in (not written up here):
- Nightly rebuild cut from 6 hours to 55 minutes

## 3. Consistency brief
- "I have a gap against the full data engineering experience requirement: I lack PySpark and distributed data processing." (cover_letter). Likely probe: Does this mean you lack data engineering experience generally? Prepare: Clarify that the profile supports 7 years of data engineering experience. Keep the specific PySpark and distributed-processing gaps explicit; do not equate Kafka volume with Spark proficiency.
- "I have no documented AI/ML engineering or Claude Code experience, nor experience with LLM-based applications or Claude Code; tested datasets and repeatable ingestion provide adjacent foundations for machine learning enablement." (cover_letter). Likely probe: Have you delivered models or LLM applications, and why is Claude Code mentioned? Prepare: The profile supports ingestion and tested data, not model or LLM delivery. The profile does not mention Claude Code; its absence is not proof of no experience. Confirm actual exposure with the candidate before elaborating.
- "- Improved data products: cut the nightly rebuild of the delivery-performance mart from 6 hours to 55 minutes with incremental dbt models." (cv). Likely probe: What exactly changed, and how was the improvement measured? Prepare: The corresponding STAR stub is unfilled. Ask the candidate to supply situation, task, action and result details. Do not write up this story or invent implementation details, measurements or tradeoffs.
- "- Own streaming data pipelines for shipment events: 40 million events per day from Kafka into PostgreSQL and BigQuery, with Apache Airflow orchestration." (cv). Likely probe: Where does Airflow orchestration sit relative to streaming ingestion, and how are failures handled? Prepare: Prepare an accurate architecture walkthrough from the candidate. The profile establishes volume, systems and ownership but does not establish delivery guarantees, replay behavior, latency or Airflow topology.
- "- Introduced data quality checks with Great Expectations on 30 core tables; failed loads dropped 70% in the first quarter." (cv). Likely probe: What counts as a failed load, and how was the comparison made? Prepare: Prepare the actual baseline, comparison method and representative checks with the candidate. The profile supports the stated outcome but supplies none of those measurement details.
- "- Defined data contracts with five producing teams and wrote the pipeline's on-call runbook for incident support." (cv). Likely probe: Can you walk through an incident you personally resolved? Prepare: Discuss the documented contract and runbook work. Obtain a real incident account from the candidate before claiming incident response actions or recovery results.
- "- Delivery: working knowledge of Terraform, GitHub Actions and Looker." (cv). Likely probe: How much did you personally implement and how deep is your tool knowledge? Prepare: Keep the working-knowledge qualification. Prepare the actual contributions behind the deployment change and Looker explore without claiming expert proficiency.
- "My earliest start is 1 February 2027, following three months' notice." (cover_letter). Likely probe: What start date can you commit to? Prepare: The profile lists earliest start 2027-02-01 and three months notice. Confirm feasibility against the actual resignation date before committing.

## 4. Honest bridges for the gaps
- **PySpark.** I do not have documented hands-on PySpark experience. I have written Python ingestion services for partner price feeds using REST and SFTP. (profile: "Wrote Python ingestion services for partner price feeds (REST and SFTP), handling about 2 million rows a day.") I would learn through guided exercises, build a small transformation pipeline, and seek review of correctness and execution behavior before taking production ownership.
- **distributed data processing.** I do not have documented hands-on distributed data processing experience. I have written Python ingestion services for partner price feeds using REST and SFTP. (profile: "Wrote Python ingestion services for partner price feeds (REST and SFTP), handling about 2 million rows a day.") I would study execution across workers, partitioning, shuffles and failure recovery, then practice on a sandbox pipeline and review its behavior with an experienced colleague.
- **data engineering experience.** The supplied gap flags data engineering experience, but my profile supports that experience. I need to clarify which part of the requirement remains unproven. I have written Python ingestion services for partner price feeds using REST and SFTP. (profile: "Wrote Python ingestion services for partner price feeds (REST and SFTP), handling about 2 million rows a day.") I would clarify the missing capability with the interviewer, study the relevant concepts, and practice a representative task with review before claiming proficiency.
- **AI/ML engineering.** I do not have documented AI/ML engineering experience. I have written Python ingestion services for partner price feeds using REST and SFTP. (profile: "Wrote Python ingestion services for partner price feeds (REST and SFTP), handling about 2 million rows a day.") I would learn the model lifecycle and data requirements, build a sandbox workflow, and seek feedback from a specialist before taking production responsibility.
- **LLM-based applications.** I do not have documented experience building LLM-based applications. I have written Python ingestion services for partner price feeds using REST and SFTP. (profile: "Wrote Python ingestion services for partner price feeds (REST and SFTP), handling about 2 million rows a day.") I would study application patterns and evaluation, build a small sandbox example, and review reliability and data handling with an experienced engineer.

## 5. Tough questions
### Why are you leaving your current role?
I want a senior individual-contributor or tech-lead role on a data platform team. I like owning a pipeline end to end, and my current work includes shipment-event pipeline ownership, mentoring and data-modelling reviews. That is the direction I want to build on. My profile does not record a separate reason for leaving; I would confirm that personal explanation before the interview.

### You lack a skill we ask for. How will you manage?
I do not have documented PySpark or distributed data processing experience. My adjacent work includes Python ingestion services, Kafka pipelines and BigQuery optimization. I would learn the execution model, practice transformations and failure handling in a sandbox, and seek experienced review before taking production ownership. I would not present adjacent experience as proficiency in the missing tools.

### Where do you see yourself in five years?
My stated goal is to become a tech lead within 3 years. I already mentor two junior engineers and lead the weekly data-modelling review. Over five years, I would want to build on that foundation through data platform ownership and technical leadership; that is a future direction rather than a claim of experience I already have.

### What is your biggest weakness?
A concrete limitation for this interview is my lack of documented Spark experience; my profile lists Spark as not yet a skill. My strongest areas are Python, SQL and Apache Airflow. I would address the gap through study, sandbox implementation and feedback, and be explicit about where I still need support.

No verified company claim was available, so 'why this company' is left for you to research.

## 6. Questions to ask
- (role) Which data product would you expect the successful candidate to take ownership of first, and what would success look like?
- (tech and growth) Which workloads require PySpark, and what level of independent proficiency is essential on joining?
- (tech and growth) What are the hardest reliability problems in your current pipelines, and how do you evaluate improvements?
- (tech and growth) What support is available for developing distributed processing skills and progressing toward technical leadership?
- (team) How is responsibility divided between this role and the platform, analytics and data science teams?
- (team) How are producer data contracts agreed, changed and enforced across teams?

## 7. Research (verified, public, professional only)
- none supplied
- 30 unverified claim(s) were not used.
- Hiring manager: nothing verified

## 8. Logistics
- Date: 2026-10-26
- Interviewers: Hiring manager
- Format: video
- Check camera, sound and light ten minutes early.
- Have a glass of water within reach.
- Clarify a vague question before answering; a five-second pause is fine.
- Close by asking whether they want anything else from you.

## 9. Notes
- Mock interview: run `jobloop mock a_p_moller_maersk__senior_data_engineer` yourself. It is interactive and not part of the unattended loop.
