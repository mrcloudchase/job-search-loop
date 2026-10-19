"""jobkey.py: the canonical key and URL form, and what must never happen (a collision, an empty or path-breaking key)."""
from __future__ import annotations

import unicodedata

import pytest

from jobloop import jobkey


def test_key_is_deterministic():
    assert jobkey.make_key("Acme, Inc.", "SOC Analyst", "https://x.io/j/1") == jobkey.make_key("Acme, Inc.", "SOC Analyst", "https://x.io/j/1")


def test_key_shape():
    assert jobkey.make_key("Acme A/S", "Data Engineer (m/f)") == "acme-a-s_data-engineer-m-f"


@pytest.mark.parametrize("text", ["A/B, (C) [D]", "x\\y:z", "  ../../etc  ", "tab\tname\nline"])
def test_slug_strips_path_breaking_punctuation(text):
    slug = jobkey.slugify(text)
    assert slug and all(c.isalnum() or c == "-" for c in slug) and not slug.startswith("-") and not slug.endswith("-")


def test_non_latin_slug_is_empty():
    assert jobkey.slugify("数据工程师") == "" and jobkey.slugify("Программист") == ""


def test_long_titles_hash_capped_and_distinct():
    a = "Senior " + "very " * 30 + "long title one"
    b = "Senior " + "very " * 30 + "long title two"
    ka, kb = jobkey.make_key("Acme", a), jobkey.make_key("Acme", b)
    assert ka != kb and len(ka) < 120 and jobkey.is_canonical(ka) and ka == jobkey.make_key("Acme", a)


def test_two_long_titles_sharing_the_first_sixty_characters_never_collide():
    prefix = "x" * 70
    assert jobkey.make_key("Acme", prefix + " one") != jobkey.make_key("Acme", prefix + " two")
    assert jobkey.make_key("c" * 80 + " one", "t") != jobkey.make_key("c" * 80 + " two", "t")


def test_non_latin_title_uses_url_job_id():
    k = jobkey.make_key("Acme", "数据工程师", "https://jobs.example/view/4461771225?utm_source=x")
    assert k == "acme_4461771225"


def test_non_latin_postings_from_one_company_never_collapse():
    keys = {jobkey.make_key("Acme", "数据工程师", f"https://jobs.example/view/{n}") for n in (1000001, 1000002, 1000003)}
    assert len(keys) == 3


def test_url_hash_fallback_stable_across_title_changes():
    url = "https://jobs.example/p/abc"
    assert jobkey.make_key("Acme", "数据工程师", url) == jobkey.make_key("Acme", "数据工程师 (新)", url) == jobkey.make_key("Acme", "", url + "?utm_x=1#top")


def test_distinct_urls_distinct_keys():
    assert jobkey.make_key("Acme", "数据", "https://j.example/a") != jobkey.make_key("Acme", "数据", "https://j.example/b")


def test_without_url_the_title_is_hashed_and_an_empty_title_is_untitled():
    assert jobkey.make_key("Acme", "数据") != jobkey.make_key("Acme", "分析") and jobkey.make_key("Acme", "数据") == jobkey.make_key("Acme", "数据")
    assert jobkey.make_key("Acme", "").startswith("acme_untitled-")


def test_mixed_script_titles_stay_distinct():
    a, b = jobkey.make_key("Acme", "Программист 1С"), jobkey.make_key("Acme", "Аналитик 1С")
    assert a != b and a.startswith("acme_1-") and b.startswith("acme_1-")


def test_lossy_title_prefers_numeric_id():
    assert jobkey.make_key("Acme", "Программист 1С", "https://l.example/jobs/view/4461771225") == "acme_1-4461771225"


def test_latin_without_decomposition_not_lossy():
    assert not jobkey.lost_letters("Ørsted Æble Straße Łódź")
    assert jobkey.make_key("Ørsted A/S", "Engineer") == "rsted-a-s_engineer"
    assert jobkey.slugify("Zürich ﬁnance") == "zurich-finance"


def test_company_fallback_distinct_and_stable():
    a, b = jobkey.make_key("腾讯", "Engineer"), jobkey.make_key("阿里巴巴", "Engineer")
    assert a != b and a == jobkey.make_key("腾讯", "Engineer", "https://other.example/1")
    assert jobkey.make_key("Сбер AI", "Engineer") != jobkey.make_key("Яндекс AI", "Engineer")
    nfd = unicodedata.normalize("NFD", "Café Ünï")
    assert jobkey.make_key("Café Ünï", "x") == jobkey.make_key(nfd, "x") == jobkey.make_key("CAFÉ ÜNÏ", "x")


def test_empty_company_is_unknown_company():
    assert jobkey.make_key("   ", "Engineer") == "unknown-company_engineer" == jobkey.make_key(None, "Engineer")


@pytest.mark.parametrize("company,title,url", [("", "", ""), ("   ", None, None), ("/", ",", "x"), ("腾", "数", ""), ("a" * 500, "b" * 500, "")])
def test_a_key_is_never_empty_and_always_canonical(company, title, url):
    key = jobkey.make_key(company, title, url)
    assert jobkey.is_canonical(key) and key.count("_") == 1 and all(part for part in key.split("_"))


@pytest.mark.parametrize("key", ["a/b_c", "a,b_c", "acme_", "_x", "a_b_c", "acme_(x)", "ACME_x", "", "plain", "a__b"])
def test_canonical_rejects_path_breaking_keys(key):
    assert not jobkey.is_canonical(key)


def test_key_safe_as_folder_name():
    key = jobkey.make_key("../../Acme/..", "a/b\\c: d, e")
    assert "/" not in key and "\\" not in key and ".." not in key and "," not in key


def test_legacy_shape_distinct_from_malformed():
    assert jobkey.is_legacy_shape("acme_role_12345") and not jobkey.is_canonical("acme_role_12345")
    assert not jobkey.is_legacy_shape("a/b_c_d") and not jobkey.is_legacy_shape("acme_role")


def test_canonical_url_drops_tracking_and_fragments_and_keeps_what_names_the_posting():
    assert (jobkey.canonical_url("HTTP://WWW.Example.com:443/Jobs/42/?utm_source=a&id=7&gclid=z&UTM_x=1#apply")
            == "https://example.com/Jobs/42?id=7")
    assert jobkey.canonical_url("https://x.io/j?b=2&a=1") == jobkey.canonical_url("https://x.io/j?a=1&b=2")
    assert jobkey.canonical_url("https://x.io/j?id=1") != jobkey.canonical_url("https://x.io/j?id=2")
    assert jobkey.canonical_url("https://x.io/a/1") != jobkey.canonical_url("https://x.io/a/2")


def test_canonical_url_leaves_what_is_not_a_url_alone():
    assert jobkey.canonical_url("  not a url ") == "not a url" and jobkey.canonical_url(None) == ""


def test_job_id_ignores_numbers_in_tracking_parameters():
    assert jobkey.job_id("https://x.io/j/55?utm_campaign=123456789") is None
    assert jobkey.job_id("https://x.io/j/7654321?utm_campaign=123456789") == "7654321"


def entries(*rows):
    return {key: {"company": c, "title": t, "url": u} for key, c, t, u in rows}


def test_audit_report_and_exit_codes():
    good = jobkey.make_key("Acme", "Engineer", "https://x.io/1")
    report = jobkey.audit(entries((good, "Acme", "Engineer", "https://x.io/1"), ("old_key_123", "Acme", "Old", "https://x.io/2"),
                                  ("drifted_key", "Acme", "Engineer", "https://x.io/3")))
    assert (report.entries, report.malformed, report.legacy, report.duplicate_urls, report.drifted) == (3, (), ("old_key_123",), (), 1)
    assert not report.failed
    bad = jobkey.audit(entries(("a/b_c", "A", "B", "https://x.io/9"), ("acme_one", "A", "One", "https://x.io/5/"), ("acme_two", "A", "Two", "https://x.io/5")))
    assert bad.malformed == ("a/b_c",) and bad.duplicate_urls == ("https://x.io/5",) and bad.failed


def test_audit_never_mutates_state():
    store = entries(("drifted", "Acme", "Engineer", "https://x.io/1"))
    before = {k: dict(v) for k, v in store.items()}
    jobkey.audit(store)
    assert store == before


@pytest.mark.parametrize("bad", [[], "x", None, {"k": "not an object"}, {"k": [1]}])
def test_audit_bad_shapes_clean_errors(bad):
    with pytest.raises(ValueError):
        jobkey.audit(bad)
