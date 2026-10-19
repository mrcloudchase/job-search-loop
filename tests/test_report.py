"""report.py: one static HTML page, everything escaped, a funnel from stage history, and a read-only run."""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import inspect
import re
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest

from jobloop import archive, report, tracker

TODAY = date(2026, 10, 19)


def ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


def put_rows(me: Path, *rows: dict) -> Path:
    path = me / "tracker.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(tracker.HEADER)
        for row in rows:
            w.writerow([row.get(c, "") for c in tracker.HEADER])
    return path


def app(company, status="applied", days=20, role="Dev", **kw):
    return {"company": company, "role": role, "status": status, "date": ago(days), **kw}


def stage(me, company, role="Dev", days=15, name="phone_screen"):
    archive.open_application(me, company, role)
    archive.record_stage(me, archive.key(company, role), on=TODAY - timedelta(days=days), stage=name)


@pytest.fixture
def me(tmp_path):
    return tmp_path / "me"


def page(me) -> str:
    path, _ = report.generate(me, TODAY)
    return path.read_text(encoding="utf-8")


def snapshot(me):
    return {str(p.relative_to(me)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(me.rglob("*"))
            if p.is_file() and p.name != report.OUTPUT}


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.attrs = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += [(tag, k, v) for k, v in attrs]


def parsed(text):
    p = Tags()
    p.feed(text)
    return p


def numbers_in(text, label):
    m = re.search(rf'<div class="stat"><b>(\d+)</b>{label}</div>', text)
    return int(m[1])


# ── the file ──
def test_default_output_path_and_dir_created(tmp_path):
    me = tmp_path / "does" / "not" / "exist"
    path, _ = report.generate(me, TODAY)
    assert path == me / "report.html" and path.is_file() and path.read_text().startswith("<!doctype html>")


def test_output_has_no_external_resources_and_no_script(me):
    put_rows(me, app("Acme", source="https://example.com/job/1"), app("Beta", "drafted"))
    out = page(me)
    tags = parsed(out)
    assert "script" not in tags.tags and "link" not in tags.tags and "img" not in tags.tags and "iframe" not in tags.tags
    assert tags.tags.count("style") == 1 and "@import" not in out and "url(" not in out
    assert not [a for a in tags.attrs if a[1] in ("src", "srcset", "data", "poster")]
    assert not [a for a in tags.attrs if a[1].startswith("on")]
    hrefs = [a for a in tags.attrs if a[1] == "href"]
    assert [h[2] for h in hrefs] == ["https://example.com/job/1"] and all(h[0] == "a" for h in hrefs)
    assert re.findall(r"https?://", out) == ["https://"]


HOSTILE = {
    "company": "<script>alert(1)</script>Evil & Co",
    "role": '"><img src=x onerror=alert(1)>',
    "sector": "</style><script>steal()</script>",
    "channel": "portal",
    "contact_person": "<b>Bob</b>",
    "notes": "'\" onmouseover='alert(1)' </style><script>x</script> <svg onload=alert(1)>",
    "source": "javascript:alert(document.cookie)",
    "deadline": (TODAY + timedelta(days=3)).isoformat(),
}


def hostile_tracker(me, status="applied"):
    put_rows(me, {**HOSTILE, "status": status, "date": ago(30)}, app("Fine", "rejected", sector="Energy"))
    archive.open_application(me, HOSTILE["company"] + "x", "Dev")


def test_hostile_values_are_escaped_in_cells_titles_and_svg(me):
    hostile_tracker(me)
    out = page(me)
    assert "<script" not in out.lower() and out.count("</style>") == 1 and "<img" not in out.lower()
    tags = parsed(out)
    assert not [t for t in tags.tags if t in ("script", "img", "iframe")] and "<b>Bob" not in out
    assert not [a for a in tags.attrs if a[1].startswith("on")]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;Evil &amp; Co" in out
    assert "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;" in out
    assert "&lt;/style&gt;&lt;script&gt;steal()" in out
    raw = re.findall(r'<span title="([^"]*)"', out)
    assert raw and all("<" not in t and ">" not in t for t in raw)
    assert "&quot; onmouseover=&#x27;alert(1)&#x27;" in raw[0]


def test_hostile_sector_is_escaped_in_svg_labels_and_aria(me):
    hostile_tracker(me)
    tags = parsed(page(me))
    aria = [a[2] for a in tags.attrs if a[1] == "aria-label"]
    assert any("</style><script>steal()</script>" in a for a in aria)
    assert all(k in ("role", "aria-label", "viewbox", "width", "preserveaspectratio", "x", "y", "class", "height", "rx")
               for t, k, _ in tags.attrs if t in ("svg", "rect", "text"))


def test_source_link_only_for_http_and_javascript_scheme_not_linked(me):
    sources = ["https://ok.example/a?x=1&y=2", "http://plain.example/", "javascript:alert(1)", "data:text/html,<script>1</script>",
               "ftp://files.example/x", "//evil.example/x", "HTTP://UP.example/p", "https://bad.example/ x onclick=1", "mailto:a@b.c"]
    put_rows(me, *[app(f"C{i}", source=s) for i, s in enumerate(sources)])
    out = page(me)
    hrefs = [a[2] for a in parsed(out).attrs if a[1] == "href"]
    assert hrefs == ["https://ok.example/a?x=1&y=2", "http://plain.example/", "HTTP://UP.example/p"]
    assert "&amp;y=2" in out and 'href="javascript' not in out
    assert "javascript:alert(1)" in out and "<a href=\"javascript" not in out


def test_every_link_opens_without_giving_the_page_to_the_opener(me):
    put_rows(me, app("A", source="https://ok.example/"))
    assert 'rel="noopener noreferrer"' in page(me)


def test_empty_cells_show_dash(me):
    put_rows(me, {"company": "Bare", "role": "Dev", "status": "applied", "date": ago(20)})
    row = re.search(r"<tr><td>Bare</td>(.*?)</tr>", page(me))[1]
    cells = re.findall(r"<td>(.*?)</td>", row)
    assert cells[0] == "Dev" and [cells[i] for i in (3, 6, 7)] == [report.DASH] * 3      # deadline, notes, source


# ── read-only, re-runnable ──
def test_report_run_leaves_tracker_and_archive_unchanged(me):
    put_rows(me, app("Acme"), app("Beta", "interview"))
    stage(me, "Beta")
    before = snapshot(me)
    page(me)
    assert snapshot(me) == before


def test_rerun_overwrites_same_path(me):
    put_rows(me, app("Acme"))
    first = page(me)
    put_rows(me, app("Acme"), app("Zeta"))
    second = page(me)
    assert "Zeta" in second and "Zeta" not in first
    assert sorted(p.name for p in me.iterdir()) == ["report.html", "tracker.csv"]


def test_a_missing_tracker_is_an_empty_report_not_an_error(me):
    out = page(me)
    assert "No application is waiting on an answer." in out and numbers_in(out, "Sent") == 0 and "n/a" in out


def test_open_flag_does_not_spawn_process():
    tree = ast.parse(inspect.getsource(report))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
    assert not imported & {"subprocess", "webbrowser", "os", "socket", "http", "urllib.request", "httpx", "smtplib"}
    p = argparse.ArgumentParser()
    report.add_arguments(p)
    assert {o for a in p._actions for o in a.option_strings} == {"-h", "--help", "--me"}


# ── the numbers ──
def mixed(me):
    put_rows(me,
             app("Hired", "hired", sector="Energy", channel="portal"),
             app("Declined", "offer_declined", sector="Energy"),
             app("Offer", "offer", sector="Health"),
             app("Interview", "interview", sector="Health", channel="online"),
             app("RejectedAfter", "rejected", sector="Energy"),
             app("RejectedCold", "rejected", sector="Retail"),
             app("Silent", "no_response", sector="Retail"),
             app("Quit", "withdrawn"),
             app("Waiting", "applied", sector="Energy", channel="portal"),
             app("Draft1", "drafted", sector="Energy"),
             app("Draft2", "drafted", sector="Ghost"))
    stage(me, "RejectedAfter")


def stats(me):
    apps, _ = report.applications(me, TODAY)
    return report.stats(apps)


def test_rejected_after_interview_counts_in_interview_bar(me):
    mixed(me)
    s = stats(me)
    assert s.funnel == {"Applied": 9, "Interview": 5, "Offer": 3, "Hired": 1}
    cold = report.applications(me, TODAY)[0]
    by = {a.row.company: report.reached(a) for a in cold}
    assert by["RejectedAfter"] == (True, True, False, False) and by["RejectedCold"] == (True, False, False, False)
    assert by["Hired"] == (True, True, True, True) and by["Declined"] == (True, True, True, False)
    assert by["Draft1"][1:] == (False, False, False)


def test_funnel_is_not_the_current_status_alone(me):
    put_rows(me, app("Gone", "rejected"))
    assert stats(me).funnel["Interview"] == 0
    stage(me, "Gone")
    assert stats(me).funnel["Interview"] == 1


def test_progress_past_screen_percentage(me):
    mixed(me)
    s = stats(me)
    assert s.past_screen == 5 / 9 and report.percent(s.past_screen) == "56%"


def test_rejection_rate_numerator_and_denominator(me):
    mixed(me)
    assert stats(me).rejection_rate == 3 / 6      # rejected x2 + no_response over hired, declined, rejected x2, no_response, withdrawn


def test_rejection_rate_with_no_final_rows_is_not_a_number_error(me):
    put_rows(me, app("A"), app("B", "interview"), app("C", "drafted"))
    assert stats(me).rejection_rate is None and report.percent(None) == "n/a"
    assert "Rejection rate: n/a" in page(me)


def test_drafted_excluded_from_all_stats_except_breakdown(me):
    mixed(me)
    s = stats(me)
    assert (s.sent, s.drafted) == (9, 2) and "Ghost" not in s.sectors and sum(s.sectors.values()) == 9
    assert sum(s.channels.values()) == 9 and s.funnel["Applied"] == 9


def test_stats_derive_only_from_inputs(me):
    mixed(me)
    s = stats(me)
    assert s.open == 3 and s.outcomes == {"hired": 1, "offer_declined": 1, "rejected": 2, "no_response": 1, "withdrawn": 1}
    assert s.sectors == {"Energy": 4, "Retail": 2, "Health": 2, "(none)": 1}
    assert s.channels == {"(none)": 6, "portal": 2, "online": 1}
    put_rows(me)
    assert stats(me).sent == 0 and stats(me).funnel == {"Applied": 0, "Interview": 0, "Offer": 0, "Hired": 0}


def test_stat_cards_present(me):
    mixed(me)
    out = page(me)
    assert [numbers_in(out, label) for label in ("Sent", "Drafted", "Open", "Reached interview", "Offers", "Hired")] == [9, 2, 3, 5, 3, 1]


def test_bucket_mapping_for_all_vocab_values():
    expect = {"drafted": "Drafted", "applied": "Active", "interview": "Interview", "offer": "Offer", "hired": "Hired",
              "rejected": "Closed", "no_response": "Closed", "offer_declined": "Closed", "withdrawn": "Closed",
              "no response": "Closed", "offer declined": "Closed", "APPLIED": "Active"}
    assert {k: report.bucket_of(k) for k in expect} == expect


def test_unknown_status_bucketed_and_named_once(me):
    put_rows(me, app("A", "ghosted"), app("B", "ghosted"), app("C", "applied"))
    out = page(me)
    assert out.count("no bucket: ghosted</p>") == 1 and report.bucket_of("ghosted") == report.UNRECOGNISED
    assert numbers_in(out, "Sent") == 3


def test_interview_only_is_treated_as_unrecognised(me):
    assert report.bucket_of("interview_only") == report.UNRECOGNISED
    put_rows(me, app("A", "interview_only"))
    assert "interview_only" in page(me) and stats(me).outcomes["hired"] == 0


def test_an_unreadable_outcome_md_is_named_on_the_page_and_the_report_still_renders(me):
    put_rows(me, app("Broken"))
    (archive.folder_for(me, "broken__dev", create=True) / "outcome.md").write_text("garbage\n")
    out = page(me)
    assert "broken__dev: outcome.md not used" in out and numbers_in(out, "Sent") == 1


# ── charts ──
def test_four_svg_charts_present_with_expected_order(me):
    mixed(me)
    out = page(me)
    assert out.count("<svg") == 4
    assert re.findall(r"<h3>(.*?)</h3>", out) == ["Funnel, by stages reached", "Outcome mix", "By sector", "By channel"]


def test_svgs_have_role_and_aria_label(me):
    mixed(me)
    svgs = [a for a in parsed(page(me)).attrs if a[0] == "svg"]
    assert sorted(k for _, k, _ in svgs).count("role") == 4 and all(v == "img" for _, k, v in svgs if k == "role")
    labels = [v for _, k, v in svgs if k == "aria-label"]
    assert len(labels) == 4 and "Applied 9, Interview 5, Offer 3, Hired 1" in labels[0]


def test_charts_render_with_one_row(me):
    put_rows(me, app("Solo", sector="Energy"))
    out = page(me)
    assert out.count("<svg") == 4 and out.count("<rect") >= 4 and "Solo" in out


def test_charts_render_with_no_rows_and_say_so(me):
    out = page(me)
    assert out.count("<svg") == 4 and "nothing to show yet" in out


def test_light_and_dark_come_from_css_variables_and_no_colour_is_hard_coded_in_the_svg(me):
    mixed(me)
    out = page(me)
    assert "prefers-color-scheme:dark" in out and "var(--bg)" in out and "var(--c0)" in out
    svg = "".join(re.findall(r"<svg.*?</svg>", out, re.S))
    assert "fill=" not in svg and "#" not in svg and "style=" not in svg


# ── the tables ──
def test_pipeline_table_columns_order(me):
    put_rows(me, app("A"))
    head = re.findall(r"<th>(.*?)</th>", page(me))
    assert head[:9] == list(report.PIPELINE_COLUMNS) and report.PIPELINE_COLUMNS[0] == "Company"


def test_notes_truncated_at_80_with_escaped_title(me):
    note = "x" * 79 + '"<' + "y" * 40
    put_rows(me, app("A", notes=note))
    out = page(me)
    m = re.search(r'<span title="(.*?)">(.*?)</span>', out)
    assert m[1] == ("x" * 79 + "&quot;&lt;" + "y" * 40) and m[2] == "x" * 79 + "…"


def test_pipeline_shows_days_quiet_from_stages_and_followups_and_leaves_out_final_and_drafted_rows(me):
    put_rows(me, app("Oldest", days=80, notes=f"{ago(80)} sent; followed up {ago(79)}"), app("Middle", days=40),
             app("Done", "rejected", days=300), app("Draft", "drafted", days=300))
    stage(me, "Middle", days=5)
    body = re.search(r"<h2>Open pipeline</h2>(.*?)<h2>", page(me), re.S)[1]
    rows = {r[0]: r for r in (re.findall(r"<td>(.*?)</td>", row) for row in re.findall(r"<tr>(<td>.*?)</tr>", body))}
    assert set(rows) == {"Oldest", "Middle"}
    assert rows["Middle"][5] == "5" and rows["Oldest"][5] == "79" and rows["Oldest"][6] == "1"


def test_pipeline_sorts_by_days_quiet_descending(me):
    put_rows(me, app("Recent", days=12), app("Oldest", days=80), app("Middle", days=40))
    body = re.search(r"<h2>Open pipeline</h2>(.*?)<h2>", page(me), re.S)[1]
    assert re.findall(r"<tr><td>(\w+)</td>", body) == ["Oldest", "Middle", "Recent"]


def test_upcoming_deadlines_are_listed_soonest_first_and_final_rows_are_left_out(me):
    d = lambda n: (TODAY + timedelta(days=n)).isoformat()
    put_rows(me, app("Later", deadline=d(20)), app("Soon", deadline=d(2)), app("Far", deadline=d(90)),
             app("Gone", "rejected", deadline=d(3)), app("MissedDraft", "drafted", deadline=d(-4)),
             app("MissedSent", "applied", deadline=d(-4)), app("Today", "drafted", deadline=d(0)))
    body = re.search(r"<h2>Upcoming deadlines</h2>(.*?)<h2>", page(me), re.S)[1]
    assert re.findall(r"<tr><td>(\w+)</td>", body) == ["MissedDraft", "Today", "Soon", "Later"]
    assert "passed, never sent" in body and "in 2 days" in body and "today" in body


def test_drafted_rows_are_listed_apart(me):
    put_rows(me, app("Draft", "drafted", sector="Energy"), app("Sent"))
    body = re.search(r"<h2>Drafted, not yet sent</h2>(.*?)<h2>", page(me), re.S)[1]
    assert "Draft" in body and "Sent" not in body


def test_footer_and_header_carry_iso_date(me):
    out = page(me)
    assert out.count(f"Generated {TODAY.isoformat()}") == 2


def test_summary_text_counts_match_stats(me):
    mixed(me)
    _, summary = report.generate(me, TODAY)
    assert "applications sent: 9; drafted, not yet sent: 2; open: 3" in summary
    assert "reached interview: 5; offers: 3; hired: 1" in summary and "56%" in summary and "50%" in summary
