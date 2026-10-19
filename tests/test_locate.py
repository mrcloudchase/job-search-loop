"""Finding the employer's own posting on an official ATS: accepted only when code can verify host, title and employer."""
from __future__ import annotations

import json

import httpx
import pytest

from jobloop import locate

DESC = ("We build data platforms for energy traders using Python, Kafka, Airflow and Terraform on Google Cloud. "
        "You design streaming pipelines, own data quality and mentor colleagues across the platform team every week. ") * 2


def site(routes):
    def handler(request: httpx.Request):
        body = routes.get(str(request.url.copy_with(query=None)))
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404)
    return httpx.MockTransport(handler)


def test_slugs_drop_legal_suffixes_and_try_variants():
    assert locate.slugs("Cmotions B.V.")[0] == "cmotions"
    assert locate.slugs("Philip Morris International")[:2] == ["philipmorris", "philip-morris"]


def test_title_score_ignores_gender_markers_and_company_tails():
    assert locate.title_score("Senior Data Platform Engineer (m/f/d)", "Senior Data Platform Engineer") == 1.0
    assert locate.title_score("Senior Data Platform Engineer", "Marketing Manager") == 0.0


def test_a_greenhouse_role_is_found_and_the_description_is_plain_text():
    jobs = {"jobs": [{"title": "Senior Data Platform Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                      "content": "&lt;p&gt;" + DESC + "&lt;/p&gt;"}]}
    result = locate.find("Acme Energy", "Senior Data Platform Engineer", DESC, transport=site(
        {"https://boards-api.greenhouse.io/v1/boards/acmeenergy/jobs": jobs}))
    assert result.found and result.found.ats == "greenhouse" and result.found.url.endswith("/acme/jobs/1")
    assert "<p>" not in result.found.description and "streaming pipelines" in result.found.description


def test_a_lever_role_is_found_when_the_text_names_the_employer():
    jobs = [{"text": "Data Engineer", "hostedUrl": "https://jobs.lever.co/acme/abc", "descriptionPlain": "Join Acme Energy today."}]
    result = locate.find("Acme Energy", "Data Engineer (m/f/d)", "", transport=site({"https://api.lever.co/v0/postings/acmeenergy": jobs}))
    assert result.found and result.found.ats == "lever" and "names Acme Energy" in result.found.why


def test_the_same_title_at_another_employer_is_refused():
    jobs = [{"text": "Data Engineer", "hostedUrl": "https://jobs.lever.co/acme/abc", "descriptionPlain": "Sell shoes."}]
    result = locate.find("Acme Energy Trading", "Data Engineer", "", transport=site({"https://api.lever.co/v0/postings/acmeenergytrading": jobs}))
    assert result.found is None and any("not the employer" in n for n in result.notes)


def test_a_different_title_is_refused():
    jobs = [{"text": "Marketing Manager", "hostedUrl": "https://jobs.lever.co/acme/abc", "descriptionPlain": "Acme Energy"}]
    assert locate.find("Acme Energy", "Data Engineer", "", transport=site({"https://api.lever.co/v0/postings/acmeenergy": jobs})).found is None


@pytest.mark.parametrize("url", ["https://evil.example/job", "https://lever.co.evil.example/x", "http://jobs.lever.co@evil.example/x"])
def test_a_link_that_is_not_on_an_ats_host_is_refused(url):
    jobs = [{"text": "Data Engineer", "hostedUrl": url, "descriptionPlain": "Acme Energy"}]
    assert locate.find("Acme Energy", "Data Engineer", "", transport=site({"https://api.lever.co/v0/postings/acmeenergy": jobs})).found is None


def test_nothing_found_says_what_was_tried_and_garbage_replies_are_ignored():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>not json</html>"))
    result = locate.find("Acme Energy", "Data Engineer", "", transport=transport)
    assert result.found is None and "lever/acmeenergy" in result.tried
    assert locate.find("", "x").found is None
