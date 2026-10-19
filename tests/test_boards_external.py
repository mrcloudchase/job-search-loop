"""boards/external.py: a person's own board runs as a separate, limited program, and what it prints is untrusted."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from jobloop import search, state
from jobloop import boards as registry
from jobloop.boards import external
from jobloop.boards.contract import BoardError, Query

HOSTS = ("jobs.nordlys.example",)
OK = {"id": "1", "title": "Embedded Engineer", "company": "Nordlys", "location": "Aarhus", "date": "2026-10-15", "deadline": None,
      "url": "https://jobs.nordlys.example/jobs/1", "description": "Write firmware."}


def adapter_source(body: str) -> str:
    return "import json, os, sys\nOK = " + repr(OK) + "\n" + body


PRINT_OK = adapter_source('print(json.dumps({"meta": {"board": "mine", "count": 1}, "results": [OK]}))\n')


def add_board(me: Path, name: str = "mine", source: str = PRINT_OK, terms: str = "", enabled: bool = False) -> Path:
    folder = external.folder(me, name)
    folder.mkdir(parents=True)
    (folder / "adapter.py").write_text(source)
    (folder / "board.json").write_text(json.dumps({"name": name, "hosts": list(HOSTS), "terms": terms, "samples": []}))
    external.register(me, name, external.digest_of(folder / "adapter.py"), "2026-10-19")
    if enabled:
        search.set_enabled(me, name, True, acknowledged_terms=True)
    return folder / "adapter.py"


def run(me, **kw):
    return registry.load("mine", me).search(Query("engineer"), **kw)


# ── what is a registered board ──
def test_a_folder_with_no_registry_entry_is_not_a_board(tmp_path):
    folder = external.folder(tmp_path, "mine")
    folder.mkdir(parents=True)
    (folder / "adapter.py").write_text(PRINT_OK)
    (folder / "board.json").write_text(json.dumps({"name": "mine", "hosts": list(HOSTS), "terms": ""}))
    with pytest.raises(ValueError, match="no built-in board"):
        registry.load("mine", tmp_path)
    assert registry.names(tmp_path) == registry.BUILT_IN


def test_a_registered_board_has_the_attributes_of_a_built_in_adapter_and_is_off_by_default(tmp_path):
    add_board(tmp_path, terms="Personal use only.")
    board = registry.load("mine", tmp_path)
    assert (board.NAME, board.HOSTS, board.TERMS, board.DEFAULT_ENABLED) == ("mine", HOSTS, "Personal use only.", False)
    assert callable(board.search) and callable(board.detail) and registry.names(tmp_path) == registry.BUILT_IN + ("mine",)
    assert registry.load("jobindex", tmp_path).NAME == "jobindex"            # built-ins are unchanged


@pytest.mark.parametrize("name", ["../evil", "..", ".", "", "a/b", "Mine", "mine ", "-x"])
def test_a_name_that_could_leave_the_boards_folder_is_refused(tmp_path, name):
    add_board(tmp_path)
    with pytest.raises(ValueError):
        registry.load(name, tmp_path)
    with pytest.raises(ValueError):
        external.folder(tmp_path, name)


def test_a_built_in_name_always_means_the_built_in_board(tmp_path):
    external.register(tmp_path, "jobindex", "0" * 64, "2026-10-19")
    assert registry.load("jobindex", tmp_path).__name__.endswith("boards.jobindex")


def test_a_damaged_board_json_or_missing_digest_refuses_the_board(tmp_path):
    adapter = add_board(tmp_path)
    (adapter.parent / "board.json").write_text(json.dumps({"name": "other", "hosts": [], "terms": ""}))
    with pytest.raises(ValueError, match="damaged"):
        registry.load("mine", tmp_path)
    (adapter.parent / "board.json").write_text("{")
    with pytest.raises(ValueError, match="damaged"):
        registry.load("mine", tmp_path)


def test_register_never_changes_an_existing_entry(tmp_path):
    external.register(tmp_path, "mine", "a" * 64, "2026-10-19")
    with pytest.raises(ValueError, match="already has an entry"):
        external.register(tmp_path, "mine", "b" * 64, "2026-10-19")
    assert json.loads((tmp_path / "boards.json").read_text())["boards"]["mine"]["digest"] == "a" * 64


def test_a_boards_file_that_is_not_a_boards_file_is_refused_and_left_alone(tmp_path):
    (tmp_path / "boards.json").write_text("{nope")
    with pytest.raises(ValueError, match="not a board settings file"):
        external.register(tmp_path, "mine", "a" * 64, "2026-10-19")
    assert (tmp_path / "boards.json").read_text() == "{nope"


# ── running it ──
def test_an_external_adapter_runs_in_a_separate_process_and_is_never_imported(tmp_path):
    add_board(tmp_path, source=adapter_source('OK2 = dict(OK, description=str(os.getpid()))\nprint(json.dumps({"results": [OK2]}))\n'))
    before = set(sys.modules)
    rows = run(tmp_path)
    assert rows[0]["description"] != str(os.getpid()) and rows[0]["title"] == "Embedded Engineer"
    assert not {m for m in set(sys.modules) - before if "adapter" in m.split(".")[-1]}


def test_an_external_adapter_never_sees_api_keys_or_the_loops_environment(tmp_path, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "HTTPS_PROXY", "AWS_SECRET_ACCESS_KEY", "HARNESS_KEY"):
        monkeypatch.setenv(name, "super-secret-value")
    add_board(tmp_path, source=adapter_source('print(json.dumps({"results": [dict(OK, description=json.dumps(sorted(os.environ)))]}))\n'))
    seen = json.loads(run(tmp_path)[0]["description"])
    assert set(seen) <= {"PATH", "LANG", "HOME", "TMPDIR", "LC_CTYPE"} and "super-secret-value" not in json.dumps(seen)


def test_the_adapter_runs_in_an_empty_scratch_folder_that_is_removed_afterwards(tmp_path):
    add_board(tmp_path, source=adapter_source('print(json.dumps({"results": [dict(OK, description=os.getcwd() + "|" + os.environ["HOME"] + "|" + str(os.listdir(".")))]}))\n'))
    cwd, home, listing = run(tmp_path)[0]["description"].split("|")
    assert cwd == home and listing == "[]" and not Path(cwd).exists()


def test_the_command_is_an_argv_list_with_no_shell_and_isolated_mode(tmp_path, monkeypatch):
    add_board(tmp_path)
    calls = []
    real = subprocess.Popen

    def spy(argv, **kw):
        calls.append((argv, kw))
        return real(argv, **kw)

    monkeypatch.setattr(subprocess, "Popen", spy)
    registry.load("mine", tmp_path).search(Query("a; rm -rf / $(id)", location="`x`", limit=7, recency_days=3))
    argv, kw = calls[0]
    assert isinstance(argv, list) and kw.get("shell") in (None, False) and argv[:2] == [sys.executable, "-I"]
    assert argv[3:] == ["search", "--query=a; rm -rf / $(id)", "--location=`x`", "--limit=7", "--recency-days=3"]
    assert kw["stdin"] == subprocess.DEVNULL


def test_a_search_with_no_recency_sends_no_recency_flag(tmp_path, monkeypatch):
    add_board(tmp_path)
    seen = []
    real = subprocess.Popen
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: seen.append(argv) or real(argv, **kw))
    registry.load("mine", tmp_path).search(Query("x", recency_days=None))
    assert not any(a.startswith("--recency-days") for a in seen[0])


def test_a_hostile_adapter_output_cannot_inject_a_non_http_url_or_another_host(tmp_path):
    hostile = [dict(OK, url=u) for u in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,x", "http://jobs.nordlys.example/x",
                                         "https://evil.example/x", "https://jobs.nordlys.example.evil.net/x", "https://user:pw@jobs.nordlys.example/x",
                                         "//jobs.nordlys.example/x", "https://jobs.nordlys.example:8443/x", "ftp://jobs.nordlys.example/x", None)]
    add_board(tmp_path, source=adapter_source(f"print(json.dumps({{'results': {hostile!r} + [OK, 'text', 5, None, {{'title': 'x'}}]}}))\n"))
    assert [r["url"] for r in run(tmp_path)] == [OK["url"]]


def test_output_is_cleaned_by_the_contract_not_trusted(tmp_path):
    add_board(tmp_path, source=adapter_source('print(json.dumps({"results": [dict(OK, title="  Big\\n  Title ", extra="x", company={"a": 1}, description="d" * 99999)]}))\n'))
    row = run(tmp_path)[0]
    assert row["title"] == "Big Title" and row["company"] is None and len(row["description"]) == 60000 and "extra" not in row


@pytest.mark.parametrize("body", ['print("not json")', 'print("[]")', 'print(json.dumps({"results": 5}))', 'print(json.dumps({"meta": {}}))', 'print("")'])
def test_output_that_is_not_the_contract_is_a_parse_error(tmp_path, body):
    add_board(tmp_path, source=adapter_source(body + "\n"))
    with pytest.raises(BoardError) as e:
        run(tmp_path)
    assert e.value.kind == "parse"


def test_an_error_line_on_stderr_becomes_a_board_error_of_that_kind(tmp_path):
    add_board(tmp_path, source=adapter_source('print("mine: rate_limited: slow down", file=sys.stderr); sys.exit(1)\n'))
    with pytest.raises(BoardError, match="slow down") as e:
        run(tmp_path)
    assert e.value.kind == "rate_limited"


@pytest.mark.parametrize("body", ['raise KeyError("x")', 'print("mine: bogus_kind: x", file=sys.stderr); sys.exit(1)',
                                  'print("mine: network: x", file=sys.stderr); sys.exit(2)', 'sys.exit(0)', 'print("mine: blocked: x", file=sys.stderr)'])
def test_a_crash_an_unknown_kind_or_the_wrong_exit_code_is_a_parse_error_never_a_result(tmp_path, body):
    add_board(tmp_path, source=adapter_source(body + "\n"))
    with pytest.raises(BoardError) as e:
        run(tmp_path)
    assert e.value.kind == "parse"


def test_an_adapter_that_runs_too_long_is_killed(tmp_path):
    with pytest.raises(BoardError, match="did not finish") as e:
        external.execute([sys.executable, "-I", "-c", "import time; time.sleep(30)"], limit=0.5)
    assert e.value.kind == "network"


def test_an_adapter_that_prints_too_much_is_killed_and_its_output_is_not_used(tmp_path):
    with pytest.raises(BoardError, match="more than 1000 bytes") as e:
        external.execute([sys.executable, "-I", "-c", "import sys\nwhile True: sys.stdout.write('x' * 10000)"], max_output=1000)
    assert e.value.kind == "parse"


def test_the_adapter_must_still_be_the_verified_one(tmp_path):
    adapter = add_board(tmp_path, source=adapter_source(f"open({str(tmp_path / 'ran')!r}, 'w').write('x')\nprint(json.dumps({{'results': [OK]}}))\n"))
    board = registry.load("mine", tmp_path)
    adapter.write_text(adapter.read_text() + "\n# changed after verification\n")
    with pytest.raises(BoardError, match="changed after it was verified"):
        board.search(Query("x"))
    assert not (tmp_path / "ran").exists()


def test_detail_returns_the_first_posting_and_refuses_a_reference_that_looks_like_a_flag(tmp_path):
    add_board(tmp_path)
    board = registry.load("mine", tmp_path)
    assert board.detail("1")["title"] == "Embedded Engineer"
    for ref in ("--help", "-x", "  "):
        with pytest.raises(BoardError) as e:
            board.detail(ref)
        assert e.value.kind == "invalid"


def test_detail_with_no_posting_is_a_parse_error(tmp_path):
    add_board(tmp_path, source=adapter_source('print(json.dumps({"results": []}))\n'))
    with pytest.raises(BoardError) as e:
        registry.load("mine", tmp_path).detail("1")
    assert e.value.kind == "parse"


# ── inside search.py ──
def test_search_runs_an_enabled_external_board_and_stores_what_it_found(tmp_path):
    add_board(tmp_path, enabled=True)
    report = search.search(["mine"], ["engineer"], tmp_path, today=date(2026, 10, 19))
    assert [o.status for o in report.outcomes] == ["ok"] and len(report.new) == 1
    stored = next(iter(state.load(tmp_path).values()))
    assert stored["board"] == "mine" and stored["title"] == "Embedded Engineer" and stored["url"] == OK["url"]


def test_a_registered_board_is_not_run_by_a_default_search_until_it_is_enabled(tmp_path):
    add_board(tmp_path)
    assert "mine" not in search.enabled_boards(tmp_path) and not search.is_enabled(tmp_path, "mine")
    search.set_enabled(tmp_path, "mine", True)
    assert "mine" in search.enabled_boards(tmp_path)


def test_an_external_board_with_terms_never_runs_without_the_acknowledgement(tmp_path):
    add_board(tmp_path, terms="Personal use only.")
    with pytest.raises(PermissionError, match="Personal use only"):
        search.search(["mine"], ["engineer"], tmp_path)
    assert state.load(tmp_path) == {}
    with pytest.raises(PermissionError):
        search.set_enabled(tmp_path, "mine", True)


def test_an_external_board_that_fails_is_one_outcome_and_the_others_still_run(tmp_path):
    add_board(tmp_path, source=adapter_source('print("mine: blocked: no", file=sys.stderr); sys.exit(1)\n'))
    report = search.search(["mine"], ["engineer"], tmp_path, today=date(2026, 10, 19))
    assert report.outcomes[0].status == "blocked" and report.new == []
