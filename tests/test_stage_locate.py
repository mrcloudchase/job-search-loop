"""The locate stage: what it proposes, what it writes, and that a model is never involved."""
from __future__ import annotations

import json

import httpx

import sim
from jobloop.engine import config as cfgmod
from jobloop.stages import locate as stage

AGG = "https://nl.whatjobs.com/job/1"


def world(tmp_path, monkeypatch, routes=None, **kw):
    posts = [sim.posting("a", "Acme Energy", "Data Engineer", status="ranked", score=80, url=AGG),
             sim.posting("b", "Ownsite Co", "Data Engineer", status="ranked", score=70, url="https://jobs.ownsite.com/1"),
             sim.posting("c", "Board Co", "Data Engineer", status="ranked", score=60, url="https://www.jobindex.dk/jobannonce/1"),
             sim.posting("d", "Low Co", "Data Engineer", status="new", url=AGG)]
    me = sim.make_me(tmp_path, postings=posts)
    ctx, server = sim.context(tmp_path, [], me=me, config=cfgmod.Config(**kw).validate())
    def handler(request):
        body = (routes or {}).get(str(request.url.copy_with(query=None)))
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404)
    monkeypatch.setattr(stage, "TRANSPORT", httpx.MockTransport(handler))
    return ctx, server


def test_only_shortlisted_postings_on_untrusted_hosts_are_proposed(tmp_path, monkeypatch):
    ctx, _ = world(tmp_path, monkeypatch)
    assert [a.key for a in stage.STAGE.propose(ctx.snapshot)] == ["acme_energy__data_engineer"]


def test_a_found_role_is_recorded_and_no_model_is_called(tmp_path, monkeypatch):
    jobs = [{"text": "Data Engineer", "hostedUrl": "https://jobs.lever.co/acme/abc", "descriptionPlain": "Join Acme Energy."}]
    ctx, server = world(tmp_path, monkeypatch, {"https://api.lever.co/v0/postings/acmeenergy": jobs})
    out = stage.STAGE.run(stage.STAGE.propose(ctx.snapshot)[0], ctx)
    data = json.loads((ctx.me / "locate" / "acme_energy__data_engineer.json").read_text())
    assert out.terminal == "success" and data["found"] and data["url"] == "https://jobs.lever.co/acme/abc" and server.requests == []
    assert stage.located(ctx.me, "acme_energy__data_engineer")["ats"] == "lever"
    assert stage.STAGE.propose(ctx.snapshot) == []                                  # and it is not looked for twice


def test_nothing_found_is_recorded_so_the_person_is_asked(tmp_path, monkeypatch):
    ctx, _ = world(tmp_path, monkeypatch)
    out = stage.STAGE.run(stage.STAGE.propose(ctx.snapshot)[0], ctx)
    data = json.loads((ctx.me / "locate" / "acme_energy__data_engineer.json").read_text())
    assert out.terminal == "success" and data["found"] is False and data["tried"]
    assert stage.located(ctx.me, "acme_energy__data_engineer") is None


def test_an_approved_application_is_not_looked_for(tmp_path, monkeypatch):
    ctx, _ = world(tmp_path, monkeypatch)
    ctx.store.add_event("human:approve_host", "acme_energy__data_engineer", {"host": "nl.whatjobs.com"}, ctx.now)
    assert stage.STAGE.propose(ctx.snapshot) == []
