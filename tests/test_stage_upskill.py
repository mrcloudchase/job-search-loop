"""stages/upskill.py: weekly cadence, a report whose resources were all fetched, gap names only to the researcher, no_op without applications."""
from __future__ import annotations

import json
from datetime import timedelta

import httpx

import sim
from jobloop import archive
from jobloop.stages import upskill as stage

FLINK = "Apache Flink documentation covers stateful stream processing with exactly-once guarantees."
SCALA = "Scala is a statically typed language for the JVM with functional features."
NOW = sim.NOW


class Site:
    def __init__(self, pages):
        self.pages, self.seen = pages, []

    def __call__(self, request):
        self.seen.append(request.url.path)
        body = self.pages.get((request.url.host, request.url.path))
        if request.url.path == "/robots.txt" or body is None:
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=f"<html><body><p>{body}</p></body></html>")


def leads(*urls):
    return {"answer": json.dumps([{"name": f"guide {i}", "url": u, "reason": "good"} for i, u in enumerate(urls)])}


def plan(fetched):
    entries = [{"gap": g, "theme": "Data platform", "kind": "hard", "hours": 10, "depends_on": [],
                "resources": [{"url": u, "name": "guide", "reason": "good"} for u in urls],
                "direction": f"You already know Python and Kafka, so skip the basics of {g}."} for g, urls in fetched.items()]
    return {"files": {"plan.json": json.dumps({"entries": entries, "order": list(fetched)})}}


def world(tmp_path, with_apps=True):
    rows = [("Acme", "Data Engineer", "applied"), ("Beta", "Platform Engineer", "applied")] if with_apps else []
    me = sim.make_me(tmp_path, tracker_rows=rows)
    for company, role, _ in rows:
        sim.write_fit(me, archive.key(company, role), 55,
                      requirements=[{"term": "Flink", "kind": "required", "backed": False}, {"term": "Scala", "kind": "required", "backed": False}])
    return me


def use_site(monkeypatch, pages):
    site = Site(pages)
    monkeypatch.setattr(stage, "WEB", {"transport": httpx.MockTransport(site), "resolve": lambda host: ["93.184.216.34"]})
    return site


def test_propose_is_cadence_driven_and_needs_a_profile(tmp_path):
    ctx, _ = sim.context(tmp_path, me=world(tmp_path))
    assert [a.key for a in stage.STAGE.propose(ctx.snapshot)] == ["*"]
    ctx.store.record_attempt(ctx.run_id, "upskill", "*", "r", "h", "2026-10-18T12:00:00+00:00", "2026-10-18T12:00:00+00:00", "success", 0, 0, [], [])
    assert stage.STAGE.propose(ctx.snapshot) == []
    later = NOW + timedelta(days=8)
    ctx2, _ = sim.context(tmp_path, me=ctx.me, now=later)
    assert len(stage.STAGE.propose(ctx2.snapshot)) == 1
    bare, _ = sim.context(tmp_path / "x", me=sim.make_me(tmp_path / "x", profile=False))
    assert stage.STAGE.propose(bare.snapshot) == []


def test_no_applications_is_a_no_op_and_writes_nothing(tmp_path):
    ctx, server = sim.context(tmp_path, me=world(tmp_path, with_apps=False))
    out = stage.STAGE.run(stage.STAGE.propose(ctx.snapshot)[0], ctx)
    assert out.terminal == "no_op" and server.requests == []
    assert not (ctx.me / "upskill").exists() and ctx.store.queue() == []


def test_report_lists_only_fetched_links_and_the_researcher_never_sees_the_profile(tmp_path, monkeypatch):
    me = world(tmp_path)
    site = use_site(monkeypatch, {("docs.example", "/flink"): FLINK, ("docs.example", "/scala"): SCALA})
    fetched = {"Flink": ["https://docs.example/flink"], "Scala": ["https://docs.example/scala"]}
    ctx, server = sim.context(tmp_path, [leads("https://docs.example/flink", "https://docs.example/gone"),
                                         leads("https://docs.example/scala"), plan(fetched)], me=me)
    out = stage.STAGE.run(stage.STAGE.propose(ctx.snapshot)[0], ctx)
    assert out.terminal == "success", out
    report = (me / "upskill" / "report-2026-10-19.md").read_text()
    assert "https://docs.example/flink" in report and "https://docs.example/scala" in report and "/gone" not in report
    assert "/gone" in site.seen
    researcher = ctx.gateway.harness("researcher", "upskill", "*")
    web = [r for r in server.requests if r["model"] == researcher]
    assert len(web) == 2 and all("Flink" in json.dumps(r) or "Scala" in json.dumps(r) for r in web)
    profile = (me / "profile.md").read_text()
    for request in web:
        text = json.dumps(request)
        assert "Rotterdam" not in text and "Tidewater" not in text and profile[:200] not in text and "alex" not in text.lower()
    queue = ctx.store.queue_item("upskill:2026-10-19")
    assert queue["status"] == "ready" and json.loads(queue["data"])["title"] == "Skills-gap report"
    assert json.loads(queue["data"])["deadline"] == ""


def test_an_unfetchable_resource_page_is_not_listed(tmp_path, monkeypatch):
    me = world(tmp_path)
    use_site(monkeypatch, {})
    ctx, _ = sim.context(tmp_path, [leads("https://docs.example/a"), leads("https://docs.example/b"),
                                    plan({"Flink": [], "Scala": []})], me=me)
    out = stage.STAGE.run(stage.STAGE.propose(ctx.snapshot)[0], ctx)
    assert out.terminal == "success"
    assert "docs.example" not in (me / "upskill" / "report-2026-10-19.md").read_text()


def test_the_stage_never_touches_the_tracker_or_profile_and_never_prompts(tmp_path, monkeypatch):
    me = world(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("input() called")))
    use_site(monkeypatch, {})
    before = sim.snapshot.fingerprint(me)
    ctx, _ = sim.context(tmp_path, [leads(), leads(), plan({"Flink": [], "Scala": []})], me=me)
    stage.STAGE.run(stage.STAGE.propose(ctx.snapshot)[0], ctx)
    assert sim.snapshot.fingerprint(me) == before


def test_a_spent_budget_is_exhausted_not_success(tmp_path, monkeypatch):
    me = world(tmp_path)
    use_site(monkeypatch, {})
    ctx, server = sim.context(tmp_path, [], me=me)
    ctx.gateway.ledger.stop = "interrupted"
    out = stage.STAGE.run(stage.STAGE.propose(ctx.snapshot)[0], ctx)
    assert out.terminal == "exhausted" and server.requests == [] and ctx.store.queue() == []


def test_no_profile_is_blocked(tmp_path):
    ctx, _ = sim.context(tmp_path, me=sim.make_me(tmp_path, profile=False))
    assert stage.STAGE.run(stage.Action("upskill", "*", "x"), ctx).terminal == "blocked"
