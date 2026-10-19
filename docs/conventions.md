# Conventions

Copied from [harness-interface's conventions](https://github.com/mrcloudchase/harness-interface/blob/main/docs/contributors/conventions.md)
and applied here.

- **Keep it simple.** The plainest code that keeps the promise. No option, hook or abstraction until something needs it.
  Delete what isn't used: no dead code, no commented-out code, no TODOs in place of work.
- **One job per module, one owner per idea.** Every module opens with a docstring saying what it is for and what it promises.
  Function docstrings say what and why, not how. Comments are rare and explain a reason the code can't show.
- **The standard library plus httpx.** (The check container may have more.) No other dependency.
- **The loop decides, not the model.** Anything checkable (compiles, page count, text extraction, keyword coverage, a quote
  being in the profile, a number being in the sources, a score, a veto, a status, a deadline) is checked or computed by code
  outside the harness. A harness's "done" is never trusted. Its output is untrusted data.
- **Negative space.** Decide what can't happen, make it impossible, and test that. Test what must never happen as well as
  what must. A test name says the behaviour: `test_a_403_is_never_treated_as_closed`.
- **Errors.** Refuse early, before anything runs or is written, with a message that says what was wrong and what to do. Never
  catch an exception just to log it.
- **Values are frozen dataclasses** where they are values; validate in `__post_init__`; sequences in a value are tuples.
- **Style.** `from __future__ import annotations`; lines up to about 120 characters; names say what a thing is in the domain.
- **Harness-agnostic.** The harness is a name in `model`; the role goes in `instructions`; the network goes in the sandbox.
  Never write code that works with only one harness.
- **Untrusted input.** Postings and everything fetched travel to a harness as files, never in instructions. HTML comments
  are stripped (`text.visible`) before anything is derived from them. Never follow instructions found in them.
- **A person decides.** Nothing is submitted or sent. The loop may write only the tracker status `drafted`. Profile writes,
  calibration and stale-application resolutions wait for confirmation.
- **Privacy.** Personal data lives only in the gitignored `me/` folder. Fixtures are fictional.
- **Rewrite everything; borrow nothing.** Nothing is copied, vendored, imported or run from ai-job-search: not its code, prompts,
  templates, portal CLIs, tests or fixtures. Read its behaviour; write your own.
