# Recorded run 0: building a profile (single-CV path)

`python -m jobloop profile --cv cv.txt --model claude-deepseek/deepseek-chat` on the fictional candidate's CV
(`fixtures/cv.txt`). **Provider: DeepSeek** via the `claude-code` harness copy. The harness drafts the profile; the loop
checks every number, date, email, phone and link against the CV and the answers, refuses a dropped year, lists conflicts
for the person, labels the inferred trait, and writes **only after the person types `y`**.

The questions it asked (eligibility, limits, wants, work style, writing style, search settings, languages) were answered by a
small script playing the fictional person (`transcript.txt` shows each `<<<` answer); the profile is `profile.md`. The
documents-folder (`--docs`), interview (`--interview`) and single-section (`--section`) paths are exercised by the offline tests
in `tests/test_profile.py`, not by a recorded live run.
