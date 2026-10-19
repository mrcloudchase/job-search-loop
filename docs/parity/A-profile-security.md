# Parity slice A: profile, onboarding, privacy, security guards, robots, web research

1. Files read in full: CLAUDE.md, README.md, SETUP.md, documents/README.md, .claude/commands/setup.md, SKILL.md + 01/02/03/09 skill files, tools/security_guards.py, robots_check.py, lint_skills.py, and tests test_setup_command, test_onboarding_privacy, test_security_guards, test_robots_check, test_placeholder_integrity, test_tools_utf8_output, test_lint_skills.
2. Path legend: `setup.md` = .claude/commands/setup.md; `SK/` = .claude/skills/job-application-assistant/; `robots_check.py`, `security_guards.py`, `lint_skills.py` live under tools/; `tests/` as named.
3. Matrix limits: the original's onboarding is a prose spec with only substring tests, so most profile rows are "prose only, no test"; the rebuild's code-checked grounding, eligibility/hours/start-date, source-tagged GitHub enrichment and "person confirms before writing" have no original text (see the last section, rows cite nearest original lines). `/expand`'s spec is outside this slice; only README:143 describes it.
4. Surprises: (a) the Languages table and Language Gate are only prose, zero tests; (b) the "confirm before writing" rule is prose in setup.md with no test, while the *privacy* preflight is order-tested; (c) security_guards' REQUIRED_IGNORE_RULES checks rule presence by exact-line set membership, and a separate test shows pattern behaviour only for upskill and interview paths; (d) robots_check docstring says "go to escalation step 3" but 09-web-research says step 4 (stale numbering); (e) test_tools_utf8_output pins UTF-8 stdout for tools outside this slice (rank_state, job_key, verify_*), only the contract carries over.
5. Row count: 211 rows across 14 topic tables (5 out-of-scope, 3 owner-addition rows with no original text).

### Onboarding paths and entry flow

| Original guarantee (file:line) | Where it lives here | How it's checked (test name) | Status |
|---|---|---|---|
| Setup offers exactly three entry paths (documents folder, single CV, interview) and all converge on one shared file-generation step and one confirmation step (setup.md:5, setup.md:434) | profile.onboard (path enum + shared finalize) | test_three_onboarding_paths_converge_on_one_finalize | planned |
| When the documents folder holds files, that path is offered first and the per-subfolder counts are shown; when empty it is still offered as a "bring materials" option (setup.md:32-68) | profile.onboard.detect_paths | test_documents_path_offered_first_only_when_folder_has_files | planned |
| Choosing the documents path on an empty folder stops with guidance on what to add and writes nothing (setup.md:68, setup.md:95) | profile.onboard.run_documents | test_documents_path_on_empty_folder_stops_without_writes | planned |
| A `--section <name>` argument skips path selection and the privacy prompt and goes straight to update-only for that section (setup.md:11, setup.md:439, README.md:270, SETUP.md:242-250) | profile.onboard.run_section | test_section_flag_skips_path_choice_and_updates_only_that_section | planned |
| Re-running documents mode is idempotent: content already present is never proposed again and nothing duplicates (setup.md:74, setup.md:435, documents/README.md:354-361) | profile.merge.build_change_sets | test_rerun_with_unchanged_documents_proposes_nothing | planned |
| The skill files are read before any extraction so the merge knows what is already there, and are held for the whole run (setup.md:97-109) | profile.merge.load_existing | test_existing_profile_loaded_before_extraction | planned |
| Documents are processed in a fixed subfolder order: cv, linkedin, diplomas, references, projects, applications (setup.md:113) | profile.ingest.ingest_folder | test_ingest_order_is_fixed | planned |
| The folder inventory is printed per subfolder (files or "(empty)") before anything is read, and the tool says it will cross-reference before proposing changes (setup.md:78-93) | profile.ingest.inventory | test_inventory_lists_every_subfolder_including_empty | planned |
| Readable formats are pdf, tex, md, txt; docx and images are not read and the person is told to convert (documents/README.md:341-350) | profile.ingest.readable_formats | test_unsupported_formats_reported_not_silently_skipped | planned (stricter: unsupported files are listed in the inventory instead of silently ignored) |
| If several LinkedIn exports exist, only the most recently modified is used (setup.md:117, documents/README.md:213) | profile.ingest.pick_linkedin_export | test_newest_linkedin_export_wins | planned |
| Application subfolders contribute only job_posting, cover_letter, cv_draft, outcome files; interview_prep files are ignored by setup (documents/README.md:332) | profile.ingest.read_applications | test_interview_prep_files_ignored_by_ingest | planned |
| Applications whose outcome status is in_progress are skipped for calibration since they have no final signal (setup.md:129, documents/README.md:330) | profile.ingest.read_applications | test_in_progress_applications_excluded_from_calibration | planned |
| The outcome file uses a fixed status vocabulary: in_progress, hired, offer_declined, rejected, no_response, interview_only (documents/README.md:313) | profile.ingest.parse_outcome | test_outcome_status_vocabulary_enforced | planned |
| The posting drop folder holds raw pasted posting text named company + title, is a scratch inbox, and its content is untrusted data not instructions (documents/README.md:267-275) | profile.ingest / guards.untrusted | test_pasted_posting_text_treated_as_data | planned |
| The application folder name is derived deterministically: lowercase, non-alphanumeric/underscore characters dropped, underscore runs collapsed, edges trimmed; an empty result stops and asks, creating nothing; result is always a single path component (documents/README.md:285-290, SK/SKILL.md:30) | archive.folder_name | test_folder_name_rules_and_empty_name_refuses | planned |
| Path B: read one CV fully, extract identity/education/experience/skills/languages/publications/awards, show a summary, then ask follow-ups for gaps before generating files (setup.md:256-262) | profile.onboard.run_single_cv | test_single_cv_path_summarises_then_asks_gaps | planned |
| Path C: conversational interview in nine sections (identity, education, experience, skills, publications/awards, behavioural, goals, references, job-search config); optional sections can be skipped (setup.md:266-349, setup.md:437) | profile.interview.sections | test_interview_sections_cover_all_nine_and_optional_skippable | planned |
| After Path A writes, gaps are asked about: goals, excitement, deal-breakers, languages (if not extracted), salary (optional), commute/location, search config (setup.md:240-248) | profile.onboard.ask_gaps | test_gap_questions_asked_only_for_missing_fields | planned |
| Path B follow-ups also cover behavioural profile, goals, deal-breakers, languages with levels, salary, references (setup.md:261) | profile.onboard.ask_gaps | test_single_cv_gap_questions_include_languages_and_deal_breakers | planned |

### Privacy preflight and personal-data protection

| Original guarantee (file:line) | Where it lives here | How it's checked (test name) | Status |
|---|---|---|---|
| Before anything is written, the remote origin is inspected (not after, since the closing note fires when files already exist) (setup.md:13-19, tests/test_onboarding_privacy.py:210-229) | guards.publish_preflight | test_origin_checked_before_any_profile_write | planned (stricter: the writer refuses to run until the preflight result is recorded) |
| A missing remote, a failing remote lookup, or a non-git directory skips the check silently (setup.md:15-17) | guards.publish_preflight | test_no_origin_or_not_a_checkout_skips_silently | planned |
| A public fork, or a remote whose visibility cannot be determined, triggers a warning and a wait for the person's confirmation before the path prompt; private/no origin/non-fork needs none (setup.md:17-30) | guards.publish_preflight | test_public_or_unknown_visibility_fork_warns_and_waits | planned (stricter: unknown visibility is treated as public by code) |
| The warning names the data at risk (name, contact, employment history, salary), says pushing to a fork exposes it, and offers two safe options: keep commits local or use a private repository (setup.md:21-27) | guards.publish_preflight.warning_text | test_warning_mentions_data_and_two_safe_options | planned |
| The closing summary carries a privacy note that the generated files are tracked and a fork is always public so commits must not be pushed there (setup.md:416-419) | profile.onboard.finalize | test_closing_summary_has_privacy_note | planned |
| README and SETUP warn about public forks immediately next to the fork command, mention personal data in tracked files, and point at the private-remote recipe (tests/test_onboarding_privacy.py:199-207, README.md:81-88, SETUP.md:184-190) | docs / guards.publish_preflight | test_docs_warn_next_to_fork_command | planned (docs content check) |
| The fork command is paired with setting the default repo so issues/PRs never silently land on the public upstream tracker (SETUP.md:166-182) | guards.publish_preflight | test_no_default_remote_pointing_at_public_upstream | planned |
| Sensitive artefacts (tracker, salary data, documents subfolders, application archives, reports, company research cache, env files, local Claude settings, ATS text extractions, tailored CV/letter files) are gitignored and never enter git (security_guards.py:66-116, SETUP.md:310) | guards.gitignore_rules | test_required_ignore_rules_all_present | planned |
| Each required ignore rule, when missing, is reported by name with an actionable message (security_guards.py:231-238, tests/test_security_guards.py:228-236) | guards.gitignore_rules | test_each_missing_ignore_rule_reported | planned |
| Extra ignore rules are allowed (tests/test_security_guards.py:239-242) | guards.gitignore_rules | test_extra_ignore_rules_do_not_fail | planned |
| Generated report ignore rules (reports, upskill markdown, depth-independent upskill report prefix) are all required (tests/test_security_guards.py:244-260) | guards.gitignore_rules | test_report_ignore_rules_required | planned |
| Any negation line (`!pattern`) outside a small reviewed allowlist fails, because a later negation can silently re-expose a path whose ignore rule is still present (security_guards.py:118-130, security_guards.py:239-247, tests/test_security_guards.py:338-358) | guards.gitignore_rules | test_unlisted_negation_fails_allowlisted_passes | planned |
| Ignore patterns are tested by behaviour, not only presence: upskill reports ignored at any depth, the skill's own SKILL.md stays tracked (tests/test_security_guards.py:279-302) | guards.gitignore_rules | test_upskill_report_ignored_at_depth_skill_md_tracked | planned (stricter: uses a real git check-ignore over every path the loop writes) |
| The path the interview command writes its prep pack to is derived from the command spec and must be ignored by the shipped rules, so moving the output fails the test (tests/test_security_guards.py:304-335) | guards.gitignore_rules | test_every_loop_output_path_is_gitignored | planned (stricter: derived from the loop's real output paths) |
| Depth-independent ignore rules exist for scraper state files because skills resolve their data directory relative to themselves (security_guards.py:69-74, 98-104) | guards.gitignore_rules | test_state_files_ignored_at_any_depth | planned |
| Template placeholders must survive in the pristine template and be destroyed by personalisation, so a CI check on committed personal data actually fires (tests/test_placeholder_integrity.py:1-30, 266-317) | profile.placeholders / guards | test_personalisation_removes_every_sentinel | planned |
| The profile's sentinel sits inside data that setup fills (the email line), not in a header comment the model may leave untouched (tests/test_placeholder_integrity.py:324-335, SK/01-candidate-profile.md:14) | profile.placeholders | test_sentinel_located_in_filled_data | planned |
| Placeholder-integrity tests only apply to the pristine upstream checkout; forks that personalised files skip them (tests/test_placeholder_integrity.py:283-286) | guards | test_placeholder_guard_skipped_on_personalised_tree | planned |
| No placeholder token may reach a compiled document: contact block and signature in CV and cover-letter templates must be personalised on every path (setup.md:375-378, tests/test_setup_command.py:58-73) | profile.placeholders.assert_none_left | test_no_placeholder_tokens_in_templates_after_setup | planned (stricter: code scans every generated file for `[YOUR_...]`-style tokens before finishing) |
| The completion summary lists each file generated including cover-letter templates (setup.md:404-414, tests/test_setup_command.py:70-73) | profile.onboard.finalize | test_summary_lists_all_generated_files | planned |
| Setup tells the person how many STAR stubs still need manual completion when the documents path left stubs (setup.md:380-381, 426-428) | profile.onboard.finalize | test_summary_reports_star_stub_count | planned |
| Generated files that carry personal data must be tracked-safe: profile files are tracked, so the privacy warning is the mitigation (setup.md:416) | guards.publish_preflight | test_profile_files_flagged_as_tracked_in_summary | planned |

### Profile structure (identity, languages, eligibility, skills, references)

| Original guarantee (file:line) | Where it lives here | How it's checked (test name) | Status |
|---|---|---|---|
| The candidate profile has fixed sections: Identity (name, location, phone, email, LinkedIn, GitHub, status, constraints), Languages, Education, Professional Experience, Independent Projects, Technical Skills, Publications, Awards, References (SK/01-candidate-profile.md:10-73) | profile.model | test_profile_schema_has_all_sections | planned |
| A Languages table lists every professional language with an honest level and optional note; levels are free text (native, C2, B1/B2 conversational, professional working) not forced to one scale (SK/01-candidate-profile.md:20-28, CLAUDE.md:21-29) | profile.model.Language | test_language_levels_accept_free_text_and_cefr | planned |
| A language not declared in the table is treated as a hard no for postings requiring it, not a gap to smooth over; a declared language at a lower level than wanted is flagged for the person, not auto-rejected (SK/01-candidate-profile.md:21-24, CLAUDE.md:25-29, README.md:316) | fit.language_gate (fed by profile.languages) | test_undeclared_language_rejects_lower_level_flags | planned |
| Languages come from the CV or LinkedIn Languages section when present (high-confidence structured source) and are asked directly otherwise, never skipped (setup.md:117, setup.md:245, 261, 275) | profile.ingest / profile.interview | test_languages_extracted_else_asked_directly | planned |
| The person is told why honesty matters for language levels: an undeclared language auto-excludes, a higher required level is flagged for their own judgment (setup.md:275) | profile.interview.language_prompt | test_language_prompt_explains_gate | planned |
| Language declarations also drive search-query language generation, so every declared language gets queries (SK/01-candidate-profile.md:21-22) | profile.queries.per_language | test_queries_generated_for_every_declared_language | planned |
| CV language is asked (English default), recorded in the identity section, governs the CV only; letters follow each posting's language (setup.md:342, CLAUDE.md:30) | profile.model.cv_language | test_cv_language_default_english_and_recorded | planned |
| Location and commute/family constraints are captured and later used to define location filter tiers (ideal, acceptable, borderline, too far) (setup.md:277, 340, 391; SK/01-candidate-profile.md:18) | profile.model.constraints, profile.queries.location_tiers | test_location_tiers_built_from_constraints | planned |
| Employment status is captured in the profile (SK/01-candidate-profile.md:17, CLAUDE.md:32) | profile.model | test_profile_has_status | planned |
| Education entries capture degree, period, institution, key topics (and thesis where applicable) (SK/01-candidate-profile.md:30-34, CLAUDE.md:35-39, setup.md:279-285) | profile.model.Education | test_education_fields | planned |
| Experience entries capture title, company, start/end, location and 3-5 bullets plus achievements and tools (SK/01-candidate-profile.md:36-44, setup.md:287-294) | profile.model.Role | test_role_fields | planned |
| Independent projects are structured entries (name, description with stack and measurable outcome), extracted from project documents and grounded in the document text (setup.md:169, tests/test_setup_command.py:125-146) | profile.ingest.projects | test_projects_ingested_into_independent_projects | planned |
| Certifications, publications, awards, references (with email/phone and relationship) are captured; references footer says more available on request (SK/01-candidate-profile.md:63-73, CLAUDE.md:54-64, setup.md:328-331) | profile.model | test_certs_publications_awards_references_roundtrip | planned |
| Referee quotes extracted from reference letters are kept as specific quotes with the referee's name, title, organisation (setup.md:121, documents/README.md:239-242) | profile.ingest.references | test_reference_quotes_carry_attribution | planned |
| Diploma documents verify official degree title, official institution spelling, graduation date and grade (setup.md:119, documents/README.md:223-227) | profile.ingest.diplomas | test_diploma_used_to_check_education_entries | planned |
| Technical skills are split into primary, secondary, domain, tools and proficiency is recorded (CLAUDE.md:48-52, SK/01-candidate-profile.md:50-61, setup.md:296-301) | profile.model.Skills | test_skills_buckets | planned |
| Strong/moderate/weak skill match areas and career goals feed the evaluation file from the profile (setup.md:366-372) | profile.to_fit_config | test_match_areas_derived_from_profile_skills | planned |
| Rich profile detail (what was done, with tools and measurable results) is the biggest quality driver, so thin input is questioned rather than accepted (README.md:350-356) | profile.interview.depth_prompts | test_thin_role_entry_triggers_followup | planned |
| Target sectors, what excites the person, and deal-breakers are stored in the main profile; language requirements are deliberately not duplicated in deal-breakers (CLAUDE.md:74-88) | profile.model.goals, profile.model.dealbreakers | test_language_not_duplicated_in_dealbreakers | planned |
| Deal-breakers are free-form lines (e.g. salary floor, on-call, parental leave terms) that carry real weight in the evaluation and veto in ranking (README.md:316, setup.md:322-325) | profile.model.dealbreakers -> fit.gates | test_free_form_dealbreaker_vetoes_posting | planned |
| Career goals and target roles/industries, what to avoid, and salary baseline (optional) are captured (setup.md:320-326) | profile.model.goals | test_goals_captured_salary_optional | planned |
| The person is nudged to describe what energised and drained them, because it shapes fit evaluation and discovered roles (README.md:365) | profile.interview | test_energy_questions_asked | planned |

### Eligibility, hours and start date (owner additions)

| Original guarantee (file:line) | Where it lives here | How it's checked (test name) | Status |
|---|---|---|---|
| No original structured field for work eligibility; the nearest original is a free-text constraints line in identity (SK/01-candidate-profile.md:18) | profile.model.eligibility (new) | test_eligibility_fields_roundtrip | planned |
| No original for hours limits or earliest start date; nearest is the commute/location constraint question (setup.md:277, 326) | profile.model.limits (new) | test_hours_and_start_date_roundtrip | planned |
| A posting that conflicts with a declared eligibility, hours cap or start-date window should be gated like a deal-breaker (nearest: deal-breakers veto, README.md:316) | fit.gates (new) | test_ineligible_posting_gated | planned |

### Cross-reference, conflicts, inference and confirmation

| Original guarantee (file:line) | Where it lives here | How it's checked (test name) | Status |
|---|---|---|---|
| Before mapping anything, source documents are checked against each other for date, title, education and employer-name mismatches (setup.md:133-140) | profile.conflicts.cross_reference | test_mismatched_dates_titles_degrees_employers_detected | planned |
| Found inconsistencies are shown as a numbered list with each version and the person must resolve each before the run continues (setup.md:142-155) | profile.conflicts.present | test_run_blocks_until_each_conflict_resolved | planned (stricter: the writer refuses while any conflict is unresolved) |
| When there are no inconsistencies the tool says so explicitly and continues (setup.md:157) | profile.conflicts.present | test_no_conflicts_message | planned |
| Proposed changes are split into additive (entirely new content) and conflicting (touches existing content but disagrees) buckets (setup.md:159-165) | profile.merge.build_change_sets | test_changes_split_additive_vs_conflicting | planned |
| Additive changes are shown grouped by target file with checkboxes, and applied in bulk only after yes, with the option to list numbers to skip (setup.md:189-215) | profile.confirm.additive | test_additive_applied_only_after_yes_with_skips | planned |
| Conflicting changes are presented one at a time showing current vs proposed with exactly three choices (keep, replace, manual); the choice is waited for each time (setup.md:217-234) | profile.confirm.conflict | test_conflict_choices_keep_replace_manual | planned |
| Nothing is written before the full change set has been presented and confirmed; only confirmed items are applied (setup.md:189, 215) | profile.confirm / profile.apply_changes | test_no_write_without_confirmation | planned (stricter: write API takes only a confirmation token produced by the confirm step) |
| Writes are targeted edits, never a whole-file rewrite; per-file results are stated, and "No changes made to X" when none (setup.md:236-238) | profile.apply_changes | test_edits_targeted_and_unchanged_files_reported | planned (stricter: diff is bounded to confirmed items) |
| Existing scored assessments in the behavioural file are never overwritten by inference (setup.md:170) | profile.apply_changes | test_existing_behavioural_scores_untouched | planned |
| Inferred behavioural additions (from LinkedIn About and reference letters) are added only under strongest behaviours, how you work best, or management style, and carry an explicit "inferred, review before relying" label (setup.md:170, 436) | profile.infer.behavioural | test_inferred_traits_labelled_and_in_allowed_sections | planned (stricter: labelling is applied by code, not by prompt) |
| The target section names for behavioural inference must be real headings in the shipped file, spelled without placeholders (tests/test_setup_command.py:96-122) | profile.model / profile.infer | test_inference_targets_exist_in_schema | planned |
| Writing-style observations from past cover letters are added only under a "patterns observed" heading, never modifying existing rules, and only when 2+ letters show the pattern (setup.md:171) | profile.infer.style | test_style_pattern_requires_two_letters | planned |
| Fit-calibration findings from posting+outcome pairs: interview/offer marks a strong-fit signal; 2+ repeated no-response/rejection pattern is noted; the scoring framework itself is never modified (setup.md:172) | calibrate | test_calibration_notes_do_not_alter_rubric | planned |
| CV profile-statement templates extracted from archived drafts must be grounded first: every claim (titles, employers, metrics, technologies) is checked against the profile and unsupported claims dropped, keeping only framing, so a drifted draft never becomes a template (setup.md:173) | profile.infer.templates + grounding | test_drifted_draft_claims_dropped_before_template | planned (stricter: deterministic claim check) |
| Cover-letter template merging adds only structurally distinct openings/bullets/closings (setup.md:174) | profile.infer.letters | test_only_new_structures_added | planned |
| Interview-prep: achievements not covered by an existing STAR example become stubs only (source, what happened, why it matters, empty S/T/A/R); full STAR answers are never drafted by this path (setup.md:175-187) | profile.infer.star_stubs | test_stubs_have_empty_star_fields_and_source | planned |
| Paths B and C do write full STAR examples (3-4) from actual experience (setup.md:380-381) | profile.interview.star | test_interview_path_writes_three_to_four_star | planned |
| Inferred traits for the behavioural profile can come from a formal assessment (PI, DISC, Myers-Briggs, StrengthsFinder) or synthesised answers to five environment/communication questions (setup.md:308-318, SK/02-behavioral-profile.md:7-8) | profile.interview.behavioural | test_behavioural_from_assessment_or_answers | planned |
| Recurring themes are extracted from LinkedIn About and recommendation text, with recommendations kept in full (setup.md:117, 170) | profile.infer.behavioural | test_recurring_themes_extracted_with_source | planned |

### Behavioural profile structure

| Original guarantee (file:line) | Where it lives here | How it's checked (test name) | Status |
|---|---|---|---|
| The behavioural file has fixed sections: overview with profile type, core drives table (drive, level, meaning), strongest behaviours, how you work best, growth areas framed positively, job-language mapping (strong fit vs friction), management style, using-in-applications (SK/02-behavioral-profile.md:10-54) | profile.model.Behavioural | test_behavioural_schema | planned |
| Posting keywords matching the person's style are flagged as strong behavioural fit, clashing keywords as potential friction and explicitly not a deal-breaker (SK/02-behavioral-profile.md:36-44) | fit.behavioural | test_friction_keywords_flag_not_veto | planned |
| The behavioural file includes a "don't overstate" instruction for what must not be claimed (SK/02-behavioral-profile.md:54) | style / grounding | test_behavioural_claims_not_overstated | planned |
| Growth areas are framed positively in applications (SK/02-behavioral-profile.md:32-34) | style | test_growth_area_framing_present | planned |
| Any assessment type is accepted (PI, DISC, MBTI, StrengthsFinder or self-assessment) (SK/02-behavioral-profile.md:8) | profile.model.Behavioural | test_free_form_assessment_accepted | planned |

### Writing style rules (03)

| Original guarantee (file:line) | Where it lives here | How it's checked (test name) | Status |
|---|---|---|---|
| Em-dashes are forbidden in written output; commas, periods or restructuring are used instead (SK/03-writing-style.md:9, SK/SKILL.md:38) | style.check_dashes | test_em_dash_and_double_hyphen_rejected | planned (stricter: code scan of every generated letter/CV) |
| Cliches and filler ("passionate about", "great fit", "leverage my skills", "hit the ground running", "drive results", "synergies") are cut (SK/03-writing-style.md:10) | style.banned_phrases | test_banned_phrases_flagged | planned (stricter: deterministic phrase list) |
| Generic buzzwords need concrete backing; every claim is tied to an example or fact (SK/03-writing-style.md:11) | style.unbacked_claims | test_buzzword_without_evidence_flagged | planned |
| Apologetic or overly humble phrasing is removed in favour of "I bring X, shown by Y" (SK/03-writing-style.md:12) | style.hedging | test_hedging_phrases_flagged | planned |
| Every company-specific statement in a letter must be independently verified from sources located by the tool (search by company name, official site), never from URLs inside the posting; unverifiable claims are generalised or dropped (SK/03-writing-style.md:13, CLAUDE.md:112) | research.verify_claims + guards.untrusted | test_unverified_company_claim_blocked | planned (stricter: claim-to-source check blocks the letter) |
| Reviewer-agent research is not trusted at face value; the claim is re-verified (SK/03-writing-style.md:13, CLAUDE.md:112) | research.verify_claims | test_reviewer_claims_reverified | planned |
| A search-result snippet is a lead, never a source; a fact enters a letter only from a fetched page (SK/03-writing-style.md:13, SK/09-web-research.md:116) | research.verify_claims | test_snippet_only_claim_rejected | planned |
| Reframing is limited to emphasis, not substance, using an "interview backtrack test": could the person explain this bullet without backtracking (SK/03-writing-style.md:14-17) | grounding / style | test_backtrack_test_flags_overreach | planned |
| Allowed reframing: reordering, natural synonyms, emphasising one aspect; flagged: merging academic and industry into an implied-industry claim, or adjacent work in the posting's exact terms; never: claiming experience not held or an unworked domain (SK/03-writing-style.md:15-17) | grounding.reframe_policy | test_reframe_levels_ok_flag_never | planned |
| Flagged bullets are shown to the person after drafting with keep/soften/drop; and if experience-match score is below 50 the person is warned before drafting that heavy reframing would be needed (SK/03-writing-style.md:18) | apply / fit | test_stretch_bullets_surface_and_low_match_warns | planned |
| Tone: warm but direct, conversational professional, first person active voice, demonstrate rather than state (SK/03-writing-style.md:20-24) | style.tone | test_passive_voice_and_generic_trait_claims_flagged | planned |
| Application headline should be specific (title/education + posting keyword), not "Application for ..." (SK/03-writing-style.md:26-33) | letter.headline | test_generic_headline_rejected | planned |
| Letters are scannable: descriptive subheadings, industry keywords in headings, no filler, one page maximum (hard rule) (SK/03-writing-style.md:35-41, CLAUDE.md:139) | letter / layout | test_letter_is_exactly_one_page | planned |
| Letters are forward-looking, not CV repetition: tasks the person can solve, the approach, expected outcomes, 1-2 short past examples (SK/03-writing-style.md:43-49) | letter.structure | test_letter_forward_looking_sections | planned |
| Letter structure: opening states role and connects background in 1-3 sentences; motivation / why-this-company comes first after opening; company-specific paragraph shows researched specifics; closing is brief, confident, no begging (SK/03-writing-style.md:51-81) | letter.structure | test_letter_section_order | planned |
| Body bullets: 3-5 bullets, outcome-oriented, at least one showing initiative, varied openings (SK/03-writing-style.md:62-65, 83-86) | letter.bullets | test_bullet_count_and_variety | planned |
| Role-type guidance (technical, domain, consulting, leadership) shapes what leads (SK/03-writing-style.md:88-105) | style.role_type | test_lead_content_depends_on_role_type | planned |
| Applications default to the posting's language and read as native, not translated (SK/03-writing-style.md:107-110, CLAUDE.md:30) | letter.language | test_letter_language_matches_posting | planned |
| Style patterns observed from past letters are additive only and never replace the rules (setup.md:171) | profile.infer.style | test_observed_patterns_additive_only | planned |
| Agentic-coding or AI-tooling mentions must name Claude Code explicitly (CLAUDE.md:103, 129) | style / letter | test_ai_tooling_mentions_name_claude_code | planned (owner: confirm still wanted for the loop) |

### Grounding and verification checklist (CLAUDE.md)

| Original guarantee (file:line) | Where it lives here | How it's checked (test name) | Status |
|---|---|---|---|
| Every claim in a CV or letter matches the profile: no fabricated skills, experience or achievements (CLAUDE.md:109) | grounding.check_claims | test_unsupported_claim_blocks_document | planned (stricter: deterministic comparison against the profile) |
| Job titles, dates, company names and locations in the output equal the profile's (CLAUDE.md:110) | grounding.check_facts | test_titles_dates_companies_match_profile | planned |
| Contact details in the output equal the profile's (CLAUDE.md:111) | grounding.check_contact | test_email_phone_links_match_profile | planned |
| Numbers, dates, emails, phones and links in a document must each appear in the sources, and no year is silently dropped (owner brief; nearest: CLAUDE.md:109-111) | grounding.check_numbers_dates | test_number_or_year_not_in_sources_blocked | planned (stricter: new, code-checked) |
| Profile statement is tailored, bullets reframed to the posting, key requirements addressed with gaps acknowledged, nice-to-haves highlighted where matched (CLAUDE.md:114-118) | apply / keywords | test_requirements_addressed_gaps_acknowledged | planned |
| Keyword coverage: exact posting terms used where truthful, supported keywords added to bullets, gaps stay visible and are never stuffed (CLAUDE.md:147, README.md:244) | keywords.coverage | test_unsupported_keyword_never_stuffed | planned |
| CV and letter must be consistent in tone and contradict nothing (CLAUDE.md:123-124) | apply.consistency | test_cv_letter_no_contradiction | planned |
| CV section headings and the references line match the CV language, not English defaults (CLAUDE.md:132) | latex / layout | test_cv_headings_localised | planned |
| Letter is addressed to the right person or "Dear Hiring Manager" if unknown (CLAUDE.md:130) | letter | test_salutation_fallback | planned |
| Both documents are compiled and the PDFs inspected; "looks fine in the .tex" is not accepted (CLAUDE.md:134-135) | pdfcheck, layout | test_documents_compiled_before_presented | planned |
| CV compiles with lualatex, letter with xelatex, or the declared command of an active custom template (CLAUDE.md:136) | latex.compile | test_engine_selected_by_template | planned |
| CV is exactly 2 pages; no orphaned entry titles; letter exactly 1 page with signature on it (CLAUDE.md:137-139) | layout.check_pages | test_page_counts_and_orphans | planned |
| Letter bullet font matches body font; lettercontent must not wrap an itemize (CLAUDE.md:140) | latex.letter_lists | test_list_wrapper_pattern | planned |
| ATS check reads the PDF text layer (pypdf then pdftotext with UTF-8); if neither extractor exists the parse checks are skipped with a warning and keywords are checked visually (CLAUDE.md:143, SETUP.md:154-164) | pdfcheck.extract_text | test_extractor_fallback_and_warning | planned |
| Text layer must have no `(cid:` markers, no replacement characters, nothing visible but missing from extraction (CLAUDE.md:144) | pdfcheck.text_layer | test_cid_and_replacement_chars_fail | planned |
| Email and phone appear as literal text in the extraction, not only as icon or hyperlink; reading order matches visual order (CLAUDE.md:145-146) | pdfcheck.contact_literals | test_contact_literal_in_text_layer | planned |
| After each document is drafted, the verification is reported as a pass/fail checklist (CLAUDE.md:106) | apply.report_checklist | test_checklist_reported_pass_fail | planned |
| The workflow is evaluate fit first, present it, then ask before drafting; downstream steps reuse a single derived company_role name (CLAUDE.md:98-99, SK/SKILL.md:27-30) | apply | test_fit_presented_before_drafting_and_name_reused | planned |
| The application is recorded in the tracker only after both documents exist; a lone CV or letter is not an application; the deadline is taken from the posting and never guessed (SK/SKILL.md:42-45) | tracker / apply | test_record_only_when_both_documents_exist | planned |
| The shared skill path must not touch the scraper's seen-jobs state (SK/SKILL.md:44) | tracker / rankstate | test_apply_does_not_modify_seen_state | planned |
| Posting full text is kept verbatim for archiving, never a summary (SK/SKILL.md:21) | archive | test_archived_posting_is_verbatim | planned |
| Interview prep gives STAR answers, role talking points and questions for the interviewer; gaps get honest answers (SK/SKILL.md:47-51, README.md:138) | interview | test_prep_pack_has_star_points_questions | planned |
| Skill frontmatter and reference table list the numbered reference files with purposes (SK/SKILL.md:55-67) | lint (new `lint` module) | test_reference_table_lists_existing_files | planned |

### Search queries organised by function

| Original guarantee (file:line) | Where it lives here | How it's checked (test name) | Status |
|---|---|---|---|
| Job search configuration collects role titles (3-8), key skills (3-5 distinctive and searchable), optional target companies, geographic scope, portals and CV language (setup.md:333-342) | profile.queries.collect | test_search_config_collects_all_inputs | planned |
| The person is first asked for the day-to-day function they want, then titles are derived from it, because titles for the same work vary across employers (setup.md:337) | profile.queries.by_function | test_titles_grouped_under_function | planned |
| Queries are organised into four priority tiers: strongest direction, domain expertise, adjacent pivots, broader roles (setup.md:392-396) | profile.queries.priority_tiers | test_queries_in_four_priority_tiers | planned |
| Placeholders in the queries file are replaced with real roles, skills, domain terms, city/country/region, and location filter tiers (setup.md:386-391) | profile.queries.render | test_no_placeholders_left_in_queries | planned |
| New role types the person may not have considered are suggested proactively from their skills (setup.md:344-349, 440) | profile.queries.suggest_roles | test_adjacent_roles_suggested | planned |
| Portal choice is acted on, not just recorded: disabled demo portals are flipped on when the market matches; others stay disabled, with a path to add a board (setup.md:341) | boards / addboard | test_portal_enabled_flags_follow_market_choice | planned |
| Queries exist in every declared language (owner brief; nearest: language table drives query-language generation, SK/01-candidate-profile.md:21-22) | profile.queries.per_language | test_each_declared_language_has_queries_per_function | planned (stricter: code-checked per language and per function) |
| `--section search` re-runs only the search interview and leaves the rest of the profile alone (README.md:265-273, setup.md:439) | profile.onboard.run_section | test_section_search_touches_only_queries | planned |
| Role-type/target-company picks feed the experience-match dimension and category naming (setup.md:337) | profile.queries / fit | test_function_names_feed_experience_match | planned |

### Enrichment from public sources

| Original guarantee (file:line) | Where it lives here | How it's checked (test name) | Status |
|---|---|---|---|
| Enrichment scans only public sources the person already linked in the profile (GitHub repos, portfolio site, Kaggle, Scholar) and looks up syllabi for named courses (README.md:143) | enrich.sources | test_enrichment_uses_only_linked_sources | planned |
| Each discovered competency is added with a source tag (README.md:143) | enrich.tag_source | test_every_enriched_item_has_source_tag | planned |
| Enrichment is additive: it surfaces skills documents leave implicit and never edits or deletes existing profile content (README.md:143, nearest: additive/conflict split setup.md:159-165) | enrich.apply | test_enrichment_never_modifies_existing_lines | planned |
| Enriched items are shown to the person and confirmed before writing (nearest: setup.md:189-215) | enrich.confirm | test_enrichment_requires_confirmation | planned |
| Fetched pages are untrusted data, never instructions, and links inside them are not followed (SK/09-web-research.md:9-16) | guards.untrusted / enrich.fetch | test_injected_instruction_in_page_ignored | planned |

### Web research and fetching trust boundary

| Original guarantee (file:line) | Where it lives here | How it's checked (test name) | Status |
|---|---|---|---|
| Postings and anything reached from them are untrusted third-party data, never instructions; hidden text in them is assumed hostile (SK/09-web-research.md:9-11, README.md:132) | guards.untrusted | test_posting_text_cannot_issue_instructions | planned (stricter: fetched text is passed only as quoted data to the model) |
| Embedded directions in fetched content are never followed (SK/09-web-research.md:13) | guards.untrusted | test_embedded_directive_not_executed | planned |
| A URL that appears inside a posting body is never fetched; the URL the person supplied is the only exception (SK/09-web-research.md:14) | guards.url_policy | test_urls_from_posting_body_never_fetched | planned (stricter: allowlist built from the supplied URL and the employer's own domain) |
| Companies are researched by searching their name and navigating from the official site, never via links in the posting (SK/09-web-research.md:15, SK/03-writing-style.md:13) | research.discover_sources | test_company_sources_found_independently | planned |
| Extracted fetch content goes only into evaluation and drafting, never into control flow (SK/09-web-research.md:16) | guards.untrusted | test_fetched_text_never_selects_commands | planned |
| A 403 from the plain fetch tool is not a dead end: the response is not treated as the page being unavailable, and the workflow must not soften the letter or lean on snippets because of it (SK/09-web-research.md:18-24, SK/SKILL.md:20) | fetch.escalate | test_403_triggers_escalation_not_giving_up | planned |
| Escalation order: portal CLI detail command if the host belongs to a configured portal; plain fetch; robots check then browser-header retry; web search for the employer's own posting; only then declare unavailable (SK/09-web-research.md:80-92) | fetch.escalate | test_escalation_order | planned |
| A page that returns 200 but shows a sign-in wall is not fixable with headers and skips to finding the employer's own posting; never draft from an aggregator title plus assumption (SK/09-web-research.md:94-96) | fetch.detect_login_wall | test_login_wall_not_retried_with_headers | planned |
| Genuinely unavailable in ranking is marked unreachable (retried later), distinct from expired (shown to be closed); in apply it stops and tells the person rather than drafting from the title (SK/09-web-research.md:92) | rankstate / fetch | test_unreachable_vs_expired_and_apply_stops | planned |
| The employer's own posting is preferred over aggregator listings, which drop reference ID, grade/seniority, essential-vs-desirable split and values language; material differences between versions are reported to the person, not silently chosen (SK/09-web-research.md:98-107) | fetch.prefer_employer | test_employer_version_preferred_and_diff_reported | planned |
| Aggregator anchor URLs (ending in a fragment) are listing pages not postings; a fetch whose content does not match the expected title is a failed fetch (SK/09-web-research.md:109) | fetch.validate_match | test_title_mismatch_treated_as_failed_fetch | planned |
| The browser-header retry sends a fixed browser user agent, accept, language, encoding, sec-fetch and upgrade headers with compression decoding, has a 45s ceiling, and writes into scratch space never the repo (SK/09-web-research.md:48-61) | fetch.browser_retry | test_retry_headers_timeout_and_scratch_dir | planned |
| Text extraction from saved HTML strips script/style/noscript/svg, unescapes entities, collapses whitespace, and is read through JSON-blob noise rather than assumed failed (SK/09-web-research.md:63-78) | fetch.html_to_text | test_html_to_text_strips_scripts_and_decodes | planned |
| Verified company claims must trace to a page actually fetched from the company's own domain or consistent independent reporting; specifics preferred over generic praise (SK/09-web-research.md:111-117) | research.verify_claims | test_claim_requires_fetched_source | planned |
| What was verified and from where is recorded in the final presentation so the person can defend it (SK/09-web-research.md:119) | research / report | test_verified_claims_listed_with_sources | planned |
| The shipped portal CLI list and per-portal permission entries are the only pre-approved network-facing commands (security_guards.py:38-64) | guards.allowed_commands | test_only_listed_commands_executable | planned (stricter: loop executes only a fixed command table) |
| Third-party skills/portals are copied by hand after reading their code; no auto-installer from third-party repos (README.md:318-324) | addboard | test_addboard_never_fetches_third_party_code | planned |
| Every tool that prints text writes UTF-8 regardless of host default encoding, so non-Latin names/titles/file names never crash a tool (tests/test_tools_utf8_output.py:1-12, 36-55) | text.utf8_io | test_non_latin_output_roundtrips_under_legacy_locale | planned |
| Specific UTF-8 output cases pinned: candidate listing, ranking apply, key audit, salary lookup company, PDF verifier error naming a CJK file, layout report of a Polish line, salary converter worksheet name (tests/test_tools_utf8_output.py:69-150) | text.utf8_io, rankstate, jobkey, pdfcheck, layout | test_cyrillic_cjk_polish_in_each_cli_output | planned |
| Commit-subject listing with non-Latin text is also clean (tests/test_tools_utf8_output.py:152-172) | not applicable | not applicable | not ported, because out of scope |

### robots.txt gate (RFC 9309, fail closed)

| Original guarantee (file:line) | Where it lives here | How it's checked (test name) | Status |
|---|---|---|---|
| The browser-header retry runs only on sites whose robots policy permits the path; it is never used to override a site that said no (SK/09-web-research.md:26-33, robots_check.py:1-5) | guards.robots.gate | test_retry_blocked_when_policy_disallows | planned |
| A disallow for either `*` or the named bot agent blocks the retry (robots_check.py:171-173, SK/09-web-research.md:41) | guards.robots.gate | test_disallow_for_either_agent_blocks | planned |
| Exit status 0 means retry may proceed and 1 means it must not; wrong argument count exits 2 and a missing URL never defaults to allowed (robots_check.py:176-182, tests/test_robots_check.py:119-128) | guards.robots.cli | test_cli_requires_url_and_exit_codes | planned |
| Longest matching rule wins regardless of file order (robots_check.py:136-144, tests/test_robots_check.py:40-49) | guards.robots.allowed | test_longest_match_beats_rule_order | planned |
| A tie in specificity between Allow and Disallow goes to Disallow, independent of which is listed first (robots_check.py:141-142, tests/test_robots_check.py:51-60) | guards.robots.allowed | test_tie_goes_to_disallow_both_orders | planned |
| Blank lines inside a record do not end it; rules after blank lines still apply (robots_check.py:93-117, tests/test_robots_check.py:33-38) | guards.robots.parse_groups | test_blank_lines_within_record_keep_rules | planned |
| The standard-library robots parser must not be used because it fails open on blank lines and file-order matching (SK/09-web-research.md:46) | guards.robots | test_no_stdlib_robotparser_import | planned |
| Wildcard `*` in patterns matches any run, trailing `$` anchors the end, a mid-pattern `$` is literal (robots_check.py:119-134, tests/test_robots_check.py:66-74) | guards.robots.match | test_wildcard_end_anchor_and_literal_dollar | planned |
| Rule patterns are percent-decoded before matching the decoded path, so encoded rules do not silently skip; the encoded rule does not over-match (robots_check.py:119-134, tests/test_robots_check.py:76-87, 214-229) | guards.robots.match | test_percent_encoded_rules_match_decoded_path | planned |
| The request path plus query string is matched (query included, decoded) (robots_check.py:147-150, tests/test_robots_check.py:76-87) | guards.robots.gate | test_query_string_is_part_of_matched_path | planned |
| An empty Disallow value means allow everything; empty or comment-only file allows (robots_check.py:127-128, tests/test_robots_check.py:89-94) | guards.robots.allowed | test_empty_disallow_and_ruleless_allow | planned |
| Comments after `#` are stripped before parsing (robots_check.py:101, tests/test_robots_check.py:96-97) | guards.robots.parse_groups | test_inline_comments_stripped | planned |
| Agent names match case-insensitively; with no group for the named agent the `*` group applies; several User-agent lines share one rule set (robots_check.py:104-117, 138, tests/test_robots_check.py:100-116) | guards.robots.parse_groups, allowed | test_agent_selection_rules | planned |
| A named-agent opt-out group is honoured even when `*` allows (tests/test_robots_check.py:101-104) | guards.robots.allowed | test_named_agent_optout_honoured | planned |
| One leading byte-order mark is skipped everywhere (grouping, body recognition, undecodable check) so a BOM does not hide the first group and flip Disallow to allow; BOM-only file is empty and allows (robots_check.py:102, 55, 75, tests/test_robots_check.py:232-265) | guards.robots | test_bom_does_not_hide_first_group | planned |
| HTTP 404 on robots.txt means no published policy and is permission (robots_check.py:158-159, SK/09-web-research.md:41) | guards.robots.gate | test_404_means_allowed | planned |
| Any other failure to read the policy (non-200/404 status, network error, timeout, curl error) leaves permission unconfirmed and the retry does not happen (robots_check.py:169-170, SK/09-web-research.md:41) | guards.robots.gate | test_unreadable_policy_is_unconfirmed_not_allowed | planned |
| A 200 whose body is not a robots file (HTML or JSON error page) is treated as unreadable, not as allow-all; an empty or whitespace-only body is genuine allow-all; a sitemap-only file counts as a policy (robots_check.py:53-71, tests/test_robots_check.py:131-212) | guards.robots.is_robots_body | test_soft_200_unconfirmed_empty_allows_sitemap_counts | planned |
| A User-agent, Allow or Disallow line containing the replacement character (undecodable bytes) makes the policy unreadable, including a stray byte before the field name or after `*`; such a character in comments or other fields is harmless (robots_check.py:73-91, tests/test_robots_check.py:268-357) | guards.robots.has_undecodable_rule | test_undecodable_rule_line_unconfirmed_comment_harmless | planned |
| Valid non-ASCII (UTF-8) rules are still parsed and obeyed (tests/test_robots_check.py:348-357) | guards.robots | test_utf8_non_ascii_rules_obeyed | planned |
| Policy is fetched first with the honest agent, then as a browser if the honest request is refused, and the browser-fetched policy is then obeyed strictly (WAFs often block the policy file itself) (robots_check.py:153-167, SK/09-web-research.md:45, tests/test_robots_check.py:158-199) | guards.robots.gate | test_browser_fallback_for_policy_read_then_strict | planned |
| The robots URL is rebuilt from scheme and host, never passed through from the caller (robots_check.py:148-151, tests/test_robots_check.py:388-405) | guards.robots.gate | test_robots_target_rebuilt_from_scheme_host | planned |
| The fetch command ends option parsing before the URL so a dash-leading value cannot become an option, and a dash-leading CLI argument fails closed (robots_check.py:36-42, tests/test_robots_check.py:361-386) | guards.robots.fetch | test_dash_url_cannot_become_flag_fails_closed | planned |
| The policy fetch follows at most 5 redirects, has a 12s limit with a 20s process ceiling, and uses a real HTTP client subprocess rather than the stdlib client because some hosts hang the latter (robots_check.py:33-42) | guards.robots.fetch | test_fetch_limits_redirects_and_timeouts | planned |
| Policy bytes are decoded as UTF-8 with replacement regardless of host locale, so an undefined legacy-codepage byte never turns a readable policy into UNCONFIRMED; a 404 HTML page with such bytes still means no policy (robots_check.py:43-46, tests/test_robots_check.py:408-459) | guards.robots.fetch | test_utf8_decode_independent_of_locale | planned |
| A non-200 response from the second (browser) attempt or last failure is reported in the UNCONFIRMED message with the reason (robots_check.py:156-169) | guards.robots.gate | test_unconfirmed_message_names_cause | planned |
| Result messages are explicit: ALLOWED with reason, DISALLOWED for named agent, UNCONFIRMED with reason; all carry a do-not-retry instruction on denial (robots_check.py:158, 169-174) | guards.robots.gate | test_gate_messages | planned |
| The robots gate is applied to every automated fetch the loop makes beyond the honest first attempt, not only the header retry (SK/09-web-research.md:28, 90) | fetch.escalate, boards/* | test_every_nonhonest_fetch_passes_robots_gate | planned (stricter: single enforced choke point) |

### Security guards (permissions, hooks, manifests)

| Original guarantee (file:line) | Where it lives here | How it's checked (test name) | Status |
|---|---|---|---|
| Every pre-approved permission entry must be in an exact reviewed allowlist; wildcard shell, network-fetch and any other extra entry fail (security_guards.py:171-216, tests/test_security_guards.py:67-80) | guards.permissions | test_unlisted_permission_fails_wildcards_named | planned (stricter: the loop has no pre-approved ambient shell at all) |
| An allowlist entry that is no longer present is only a note, not a failure (security_guards.py:217-220, tests/test_security_guards.py:81-88) | guards.permissions | test_dropped_allowlisted_permission_passes | planned |
| Portal CLI approvals are narrowed to one entry per shipped CLI path, not a blanket "run any file" (security_guards.py:42-52) | guards.allowed_commands | test_each_board_adapter_listed_individually | planned |
| Unreadable or invalid settings JSON fails with a clear message and no traceback (security_guards.py:172-177, tests/test_security_guards.py:89-93) | guards.permissions | test_invalid_settings_json_fails_cleanly | planned |
| Malformed settings shapes (non-object root, non-object permissions, non-list or non-string allow) fail cleanly (security_guards.py:178-208, tests/test_security_guards.py:95-107) | guards.permissions | test_malformed_settings_shapes_fail_cleanly | planned |
| Hooks in settings are held to an allowlist (empty by design) because they run automatically on events without any prompt; any hook command fails (security_guards.py:132-142, 184-199, tests/test_security_guards.py:128-139) | guards.hooks | test_any_hook_fails_unless_allowlisted | planned (stricter: loop refuses to start with any hook config present) |
| Hook check runs before the permissions shape guards so a malformed permissions block cannot return early and skip it (security_guards.py:182-183, tests/test_security_guards.py:141-156) | guards.hooks | test_hook_caught_when_permissions_malformed | planned |
| Every hook event type is checked; every command in a multi-command event is reported (tests/test_security_guards.py:158-181) | guards.hooks | test_all_events_and_all_commands_reported | planned |
| Unrecognised hook shapes (string events, non-list entries, missing hooks list, missing/non-string command) fail closed with a marker that cannot be allowlisted; a non-object hooks value fails cleanly (security_guards.py:147-168, tests/test_security_guards.py:182-201) | guards.hooks | test_unrecognised_hook_shapes_fail_closed | planned |
| Absent or empty hooks pass; an explicitly allowlisted hook passes (tests/test_security_guards.py:203-225) | guards.hooks | test_absent_hooks_pass_allowlisted_passes | planned |
| Portal CLI manifests must not declare install-time lifecycle scripts (preinstall, install, postinstall, prepare, prepack) or trustedDependencies (security_guards.py:144, 250-280, tests/test_security_guards.py:361-381) | guards.manifests | test_lifecycle_scripts_and_trusted_dependencies_fail | planned |
| Benign scripts pass; manifests inside node_modules are ignored; malformed manifest shapes fail cleanly (security_guards.py:251-269, tests/test_security_guards.py:383-410) | guards.manifests | test_benign_scripts_pass_nodemodules_ignored | planned |
| Finding zero manifests is itself a failure (glob roots wrong or tree moved) (security_guards.py:254-255, tests/test_security_guards.py:412-416) | guards.manifests | test_no_manifests_found_fails | planned |
| No tracked path may contain a node_modules component (including symlinks or files named that); skipped quietly when not a git checkout or git missing (security_guards.py:283-308, tests/test_security_guards.py:419-456) | guards.tracked_node_modules | test_tracked_node_modules_fails_untracked_passes | planned |
| The guard script exits 1 with a failure list or 0 with an OK line naming the covered checks; the real repo passes (security_guards.py:311-325, tests/test_security_guards.py:60-64, 458-462) | guards.run_all | test_clean_tree_passes_and_real_repo_passes | planned |
| Intentional widening requires updating the allowlist in the same diff so the change is explicit and reviewable (security_guards.py:6-10, 211-215) | guards (design rule) | test_failure_message_names_allowlist_to_update | planned |
| The shipped permission and settings design is least-privilege: broad earlier local settings are warned about and removed (SETUP.md:346-351) | guards.permissions | test_no_broad_local_settings_present | planned |
| Shared skill/command lint: each skill has parseable YAML frontmatter with non-empty name and description (lint_skills.py:35-55, tests/test_lint_skills.py:154-160) | lint.check_skill | test_skill_frontmatter_requires_name_and_description | planned |
| Missing/unterminated frontmatter and non-mapping frontmatter each fail with a specific message (lint_skills.py:37-50) | lint.check_skill | test_bad_frontmatter_variants_fail | planned |
| Skill allowed-tools entries that run a bun script must point at an existing file, glob entries must match at least one file (lint_skills.py:56-70, tests/test_lint_skills.py:124-152) | lint.check_skill | test_allowed_tools_target_must_exist | planned |
| Every command file starts with a `# /<name>` title (lint_skills.py:73-77, tests/test_lint_skills.py:162-169) | lint.check_command | test_command_title_format | planned |
| Settings JSON must be an object with permissions an object and allow a list; each shape violation fails cleanly (lint_skills.py:80-95, tests/test_lint_skills.py:66-113) | lint.check_settings | test_settings_shape_errors_clean | planned |
| Lint fails when no skills or no commands are found (lint_skills.py:98-103) | lint.run | test_empty_tree_fails | planned |
| Lint reports a count of skills and commands on success (lint_skills.py:117) | lint.run | test_lint_ok_summary | planned |
| Framework files carry version markers bumped when they change, so updates can be previewed per file (SK/SKILL.md:8, SK/01:2, SK/03:2, SK/09:2, README.md:208-209) | lint / not ported | not applicable | not ported, because the rebuild ships one versioned package rather than per-file markers |

### Out of scope items found in this slice

| Original guarantee (file:line) | Where it lives here | How it's checked (test name) | Status |
|---|---|---|---|
| /reset command to wipe profile data or documents (README.md:332-342, README.md:149) | n/a | n/a | not ported, because out of scope |
| /gmail-sync command (README.md:141) | n/a | n/a | not ported, because out of scope |
| /notion-sync command (README.md:140) | n/a | n/a | not ported, because out of scope |
| check_upstream_updates tool and update preview (README.md:346, SETUP.md:315-317) | n/a | n/a | not ported, because out of scope |
| upstream_triage tool and weekly watch workflow (README.md:346, SETUP.md:319-327) | n/a | n/a | not ported, because out of scope |
