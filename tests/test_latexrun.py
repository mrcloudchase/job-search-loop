"""latexrun.py: the allowlisted compile command, LaTeX's own errors, template folders, and the real container."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jobloop import latexrun as L

TEMPLATES = Path(L.__file__).parent / "templates"
CV_SAMPLE = (TEMPLATES / "cv" / "sample.tex").read_text()
LETTER_SAMPLE = (TEMPLATES / "letter" / "sample.tex").read_text()
CV_TEMPLATE = (TEMPLATES / "cv" / "template.tex").read_text()
LETTER_TEMPLATE = (TEMPLATES / "letter" / "template.tex").read_text()
CLS = (TEMPLATES / "letter" / "jobletter.cls").read_text()


def make_folder(tmp_path, **changes):
    folder = tmp_path / "mine"
    folder.mkdir()
    manifest = {"name": "my-cv", "kind": "cv", "source": "template.tex", "sample": "sample.tex",
                "extension": ".tex", "compile": "lualatex", "page_limit": 2}
    manifest.update(changes)
    (folder / "template.json").write_text(json.dumps(manifest))
    (folder / "template.tex").write_text("\\documentclass{article}\n")
    (folder / "sample.tex").write_text("\\documentclass{article}\n")
    return folder


def rendered(**kw):
    base = dict(name="cv", ok=True, pdf=b"%PDF", log_tail="", errors=(), timed_out=False, pdfinfo="Pages:           2\n",
                text="", text_raw="", bbox="", fonts="", tool_errors=())
    base.update(kw)
    return L.Rendered(**base)


# ---- the compile command ----

def test_compile_command_is_built_only_from_an_allowed_engine():
    assert L.compile_argv("xelatex", "cover.tex") == ["xelatex", "-interaction=nonstopmode", "-no-shell-escape", "cover.tex"]
    for engine in ("sh", "latexmk", "lualatex; rm -rf /", "/usr/bin/xelatex", "typst", ""):
        with pytest.raises(L.TemplateError, match="not allowed"):
            L.compile_argv(engine, "cv.tex")


def test_compile_source_must_be_a_plain_tex_file_name():
    for source in ("../cv.tex", "/etc/passwd.tex", "cv.sh", "cv", "-x.tex", "a b.tex", "a;b.tex", ".hidden.tex"):
        with pytest.raises(L.TemplateError):
            L.compile_argv("lualatex", source)


def test_shell_escape_is_never_turned_on_by_any_engine():
    for engine in L.ENGINES:
        argv = L.compile_argv(engine, "cv.tex")
        assert "-no-shell-escape" in argv
        assert not any(a in ("-shell-escape", "--shell-escape", "-enable-write18") for a in argv)


def test_the_container_is_confined():
    argv = L.docker_argv("img:1", Path("/x"), "n", {"mode": "compile"})
    joined = " ".join(argv)
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv and argv[argv.index("--user") + 1] == "1000:1000"
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "--privileged" not in argv and "/var/run/docker.sock" not in joined
    assert "/x:/in:ro" in argv and ":rw" not in "".join(a for a in argv if a.startswith("/x"))


# ---- reading LaTeX's errors ----

LOG = """This is LuaTeX
(./cv.tex
! Undefined control sequence.
l.12 \\fooo
           bar
Some chatter
! LaTeX Error: Environment itemze undefined.

See the LaTeX manual or LaTeX Companion for explanation.
Type  H <return>  for immediate help.
 ...
l.20 \\begin{itemze}
                   
No pages of output.
"""


def test_latex_errors_are_returned_word_for_word():
    errors = L.extract_errors(LOG)
    assert len(errors) == 2
    assert errors[0] == "! Undefined control sequence.\nl.12 \\fooo\n           bar"
    assert errors[1].startswith("! LaTeX Error: Environment itemze undefined.")
    assert "l.20 \\begin{itemze}" in errors[1]


def test_a_clean_log_has_no_errors():
    assert L.extract_errors("(./cv.tex) Output written on cv.pdf (2 pages).\n") == ()


def test_a_failed_compile_reports_the_errors_not_the_whole_log():
    report = rendered(ok=False, pdf=None, errors=L.extract_errors(LOG), log_tail="tail").failure_report()
    assert "! Undefined control sequence." in report and "tail" not in report


def test_a_failure_without_error_lines_reports_the_log_tail():
    assert "the end of the log" in rendered(ok=False, log_tail="the end of the log").failure_report()


def test_a_timeout_is_reported_as_one():
    assert "ran out of time" in rendered(ok=False, timed_out=True).failure_report()


def test_log_tail_keeps_only_the_last_lines():
    tail = L.log_tail("\n".join(str(i) for i in range(100)), 5)
    assert tail == "95\n96\n97\n98\n99"


def test_page_count_is_read_from_pdfinfo():
    assert rendered().pages == 2
    assert rendered(pdfinfo="").pages is None


# ---- template folders ----

def test_the_built_in_templates_load_and_declare_their_toolchain():
    cv, letter = L.builtin_template("cv"), L.builtin_template("letter")
    assert (cv.compile, cv.page_limit, cv.extension) == ("lualatex", 2, ".tex")
    assert (letter.compile, letter.page_limit, letter.extension) == ("xelatex", 1, ".tex")
    assert "jobletter.cls" in letter.support_files() and cv.support_files() == {}


def test_a_valid_own_template_folder_loads(tmp_path):
    t = L.load_template(make_folder(tmp_path))
    assert (t.name, t.compile, t.page_limit) == ("my-cv", "lualatex", 2)


@pytest.mark.parametrize("change,message", [
    ({"name": "My CV"}, "kebab-case"),
    ({"name": "my_cv"}, "kebab-case"),
    ({"extension": ".typ", "source": "template.typ", "sample": "sample.typ"}, "not supported"),
    ({"compile": "typst compile template.typ"}, "not allowed"),
    ({"compile": "rm -rf /"}, "not allowed"),
    ({"kind": "poster"}, "kind"),
    ({"page_limit": 0}, "page_limit"),
    ({"page_limit": "2"}, "page_limit"),
    ({"page_limit": True}, "page_limit"),
    ({"source": "../template.tex"}, "plain file name"),
    ({"source": "sample.tex"}, "different files"),
    ({"sample": "missing.tex"}, "missing"),
])
def test_an_invalid_template_folder_is_refused(tmp_path, change, message):
    with pytest.raises(L.TemplateError, match=message):
        L.load_template(make_folder(tmp_path, **change))


def test_a_manifest_with_unknown_or_missing_keys_is_refused(tmp_path):
    folder = make_folder(tmp_path)
    manifest = json.loads((folder / "template.json").read_text())
    (folder / "template.json").write_text(json.dumps({**manifest, "command": "sh"}))
    with pytest.raises(L.TemplateError, match="exactly these keys"):
        L.load_template(folder)
    (folder / "template.json").write_text("{nope")
    with pytest.raises(L.TemplateError, match="not valid JSON"):
        L.load_template(folder)


def test_a_folder_without_a_manifest_is_refused(tmp_path):
    with pytest.raises(L.TemplateError, match="template.json"):
        L.load_template(tmp_path)


def test_a_fontspec_template_cannot_declare_pdflatex(tmp_path):
    folder = make_folder(tmp_path, compile="pdflatex")
    (folder / "template.tex").write_text("\\usepackage{fontspec}\n")
    with pytest.raises(L.TemplateError, match="fontspec"):
        L.load_template(folder)
    (folder / "template.json").write_text((folder / "template.json").read_text().replace("pdflatex", "xelatex"))
    assert L.load_template(folder).compile == "xelatex"


def test_a_companion_file_using_fontspec_also_blocks_pdflatex(tmp_path):
    folder = make_folder(tmp_path, compile="pdflatex")
    (folder / "mine.cls").write_text("\\RequirePackage{fontspec}\n")
    with pytest.raises(L.TemplateError, match="mine.cls"):
        L.load_template(folder)


def test_a_symlink_in_a_template_folder_is_refused(tmp_path):
    folder = make_folder(tmp_path)
    (folder / "leak.sty").symlink_to("/etc/passwd")
    with pytest.raises(L.TemplateError, match="symlink"):
        L.load_template(folder).support_files()


def test_a_cv_template_cannot_be_used_as_the_letter(tmp_path):
    with pytest.raises(L.TemplateError, match="cv template"):
        L.compile_documents("x", "y", cv_template=L.builtin_template("letter"))


def test_a_custom_templates_declared_engine_and_files_are_what_runs(tmp_path, monkeypatch):
    folder = make_folder(tmp_path, compile="xelatex")
    (folder / "mine.cls").write_text("% class\n")
    seen = {}

    def fake_run(image, files, spec, label, timeout):
        seen.update(image=image, files=files, spec=spec)
        return rendered()

    monkeypatch.setattr(L, "_run", fake_run)
    L.compile_one("body", L.load_template(folder), "cv", "img:9", 30)
    assert seen["spec"]["argv"] == ["xelatex", "-interaction=nonstopmode", "-no-shell-escape", "cv.tex"]
    assert seen["files"] == {"mine.cls": b"% class\n", "cv.tex": b"body"} and seen["image"] == "img:9"


def test_the_default_engines_are_lualatex_for_the_cv_and_xelatex_for_the_letter(monkeypatch):
    engines = {}
    monkeypatch.setattr(L, "_run", lambda image, files, spec, label, timeout: engines.setdefault(label, spec["argv"][0]) and rendered())
    L.compile_documents("a", "b")
    assert engines == {"cv": "lualatex", "cover": "xelatex"}


def test_a_template_may_not_ship_a_file_named_like_the_document(tmp_path):
    folder = make_folder(tmp_path)
    (folder / "cv.tex").write_text("x")
    with pytest.raises(L.TemplateError, match="document's own name"):
        L.compile_one("body", L.load_template(folder), "cv", "img", 5)


def test_a_template_is_not_registered_when_its_sample_does_not_compile(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_run", lambda *a: rendered(ok=False, pdf=None, errors=("! Boom.",)))
    with pytest.raises(L.TemplateError, match="(?s)does not compile.*! Boom"):
        L.verify_template(L.load_template(make_folder(tmp_path)))


def test_a_template_is_not_registered_when_its_sample_has_the_wrong_page_count(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_run", lambda *a: rendered(pdfinfo="Pages:           3\n"))
    with pytest.raises(L.TemplateError, match="3 pages; its page_limit is 2"):
        L.verify_template(L.load_template(make_folder(tmp_path)))


def test_a_template_whose_sample_compiles_to_its_limit_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_run", lambda *a: rendered())
    assert L.verify_template(L.load_template(make_folder(tmp_path))).pages == 2


def test_the_default_page_limits_are_two_for_a_cv_and_one_for_a_letter():
    assert L.KINDS == {"cv": ("lualatex", 2), "letter": ("xelatex", 1)}


def test_the_shipped_templates_hold_no_personal_data():
    for text in (CV_TEMPLATE, LETTER_TEMPLATE):
        assert not re.search(r"@(?!example)\w|\+\d{2} ?\d|https?://", text.replace("[[URL]]", ""))
        assert "[[FULL NAME]]" in text or "[[FIRST NAME]]" in text
    for text in (CV_SAMPLE, LETTER_SAMPLE):
        assert "example.invalid" in text


# ---- what the template files promise (the rows' look and structure) ----

def test_cv_uses_moderncv_banking_blue_11pt_a4_sans():
    for text in (CV_TEMPLATE, CV_SAMPLE):
        assert "\\documentclass[11pt,a4paper,sans]{moderncv}" in text
        assert "\\moderncvstyle{banking}" in text and "\\moderncvcolor{blue}" in text


def test_cv_preamble_forces_the_name_and_headings_blue():
    for text in (CV_TEMPLATE, CV_SAMPLE):
        for needed in ("\\renewcommand*{\\namefont}", "\\colorlet{firstnamecolor}", "\\colorlet{familynamecolor}",
                       "\\colorlet{namecolor}", "\\renewcommand*{\\sectionstyle}"):
            assert needed in text


def test_cv_hypersetup_is_in_atendpreamble_with_usenone():
    for text in (CV_TEMPLATE, CV_SAMPLE):
        assert re.search(r"\\AtEndPreamble\{%?\s*\\hypersetup\{pdfpagemode=UseNone", text)


def test_cv_geometry_is_never_shrunk_below_the_stock_scale_and_samples_agree():
    scales = {re.search(r"\\usepackage\[scale=([\d.]+)\]\{geometry\}", t).group(1) for t in (CV_TEMPLATE, CV_SAMPLE)}
    assert scales == {"0.80"}
    assert "\\vspace" not in CV_TEMPLATE.split("\\begin{document}")[1]


def test_cv_loads_no_font_encoding_package_because_it_is_lualatex_only():
    assert "fontenc" not in CV_TEMPLATE and "fontenc" not in CV_SAMPLE


SECTIONS = ["Profile", "Core Competencies", "Professional Experience", "Education", "Languages", "Publications",
            "Honors and Awards", "References"]


def test_cv_skeleton_sections_are_present_in_the_declared_order():
    for text, wrap in ((CV_TEMPLATE, "[[%s]]"), (CV_SAMPLE, "%s")):
        found = re.findall(r"\\section\{(.*?)\}", text)
        assert found == [wrap % s for s in SECTIONS]


def test_cv_template_tells_the_drafter_the_rules_it_must_keep():
    head = CV_TEMPLATE.split("\\documentclass")[0]
    for rule in ("Escape \\& \\% \\$ \\# \\_", "\\item {[text]}", "never \"--\"", "No \\vspace between \\item",
                 "address line with no address is deleted", "translated"):
        assert rule in head


def test_cv_dates_use_a_single_hyphen_and_never_vspace_between_items():
    for date in re.findall(r"\\cventry\{(.*?)\}", CV_SAMPLE):
        assert "--" not in date and re.fullmatch(r"\d{4} - (\d{4}|present)", date)
    assert "\\vspace" not in CV_SAMPLE


def test_cv_sample_has_a_competency_list_of_five_to_seven_bold_labels():
    block = CV_SAMPLE.split("\\section{Core Competencies}")[1].split("\\section")[0]
    assert 5 <= len(re.findall(r"\\item \\textbf\{", block)) <= 7


def test_letter_has_every_structural_block_in_order():
    order = ["\\jlname", "\\jlcontact", "\\jlreference", "\\makeletterhead", "\\letterdate", "\\salutation",
             "\\lettercontent", "\\begin{letterbullets}", "\\closingline", "\\signoff"]
    for text in (LETTER_TEMPLATE, LETTER_SAMPLE):
        positions = [text.index(m) for m in order]
        assert positions == sorted(positions)
        assert "\\documentclass{jobletter}" in text


def test_letter_template_carries_the_reference_id_place_and_language_agnostic_labels():
    assert "\\jlreference{[[Ref.: ID]]}" in LETTER_TEMPLATE
    assert "language of the POSTING" in LETTER_TEMPLATE and "Med venlig hilsen" in LETTER_TEMPLATE
    assert not re.search(r"Dear|Sincerely|Date:", CLS)


def test_letter_class_sets_the_look_geometry_fonts_and_colours():
    assert "\\LoadClass[11pt,a4paper]{article}" in CLS and "margin=2.3cm" in CLS
    assert "{2B2B2B}" in CLS and "{6A6A6A}" in CLS
    assert "Lato-Light" in CLS and "Raleway-Medium" in CLS and "\\fontsize{40}{44}" in CLS
    assert CLS.count("\\fontsize{11}{13}") >= 4 and "\\fontsize{11}{14}" in CLS
    assert "\\pagestyle{empty}" in CLS


def test_letter_bullets_are_set_in_the_body_font_by_the_environment_itself():
    env = CLS.split("\\newenvironment{letterbullets}")[1].split("\\closingline")[0]
    assert "\\jlbodyfont" in env and "itemize" in env
    assert "\\begin{itemize}" not in LETTER_TEMPLATE and "\\begin{itemize}" not in LETTER_SAMPLE


def test_letter_closing_has_no_trailing_line_break():
    assert "\\\\" not in CLS.split("\\newcommand*{\\signoff}")[1]
    assert "\\signoff{[[Yours sincerely,]]}{[[FULL NAME]]}\n" in LETTER_TEMPLATE


def test_letter_sample_has_three_to_five_bullets_with_bold_labels():
    bullets = re.findall(r"\\item (.*)", LETTER_SAMPLE)
    assert 3 <= len(bullets) <= 5 and all(b.startswith("\\textbf{") for b in bullets)


def test_letter_class_has_no_page_margin_or_spacing_override_hooks():
    assert "setspace" not in CLS and "\\linespread" not in CLS


# ---- the real container ----

def docker_image_present():
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "image", "inspect", L.DEFAULT_IMAGE], capture_output=True).returncode == 0


needs_image = pytest.mark.skipif(not docker_image_present(), reason=f"{L.DEFAULT_IMAGE} is not built")


@pytest.fixture(scope="module")
def documents():
    return L.compile_documents(CV_SAMPLE, LETTER_SAMPLE)


@pytest.mark.docker
@needs_image
def test_the_cv_compiles_to_two_pages_and_the_letter_to_one(documents):
    assert documents.cv.ok and documents.cv.pages == 2, documents.cv.failure_report()
    assert documents.cover.ok and documents.cover.pages == 1, documents.cover.failure_report()
    assert documents.cv.pdf.startswith(b"%PDF") and documents.cover.pdf.startswith(b"%PDF")
    assert documents.cv.tool_errors == () and documents.cover.tool_errors == ()


@pytest.mark.docker
@needs_image
def test_both_documents_extract_as_text_in_every_form(documents):
    assert "Professional Experience" in documents.cv.text and "Professional Experience" in documents.cv.text_raw
    assert "Dear Hiring Manager," in documents.cover.text and "Ref.: EX-2026-014" in documents.cover.text_raw
    assert "<word" in documents.cv.bbox and "<page" in documents.cover.bbox
    assert "• Data pipelines" in documents.cv.text and "M +45 00 00 00 00" in documents.cv.text


@pytest.mark.docker
@needs_image
def test_the_letter_embeds_the_open_fonts_from_the_image(documents):
    for font in ("Lato-Light", "Raleway-Medium", "Raleway-Bold"):
        assert font in documents.cover.fonts


@pytest.mark.docker
@needs_image
def test_ligatures_survive_in_the_text_layer_of_both_engines():
    words = "official difficult affluent finish fluffy"
    cv = CV_SAMPLE.replace("Backend engineer with eight years", words + ". Backend engineer with eight years")
    letter = LETTER_SAMPLE.replace("I am applying", words + ". I am applying")
    docs = L.compile_documents(cv, letter)
    for doc in (docs.cv, docs.cover):
        assert doc.ok, doc.failure_report()
        assert words in doc.text and words in doc.text_raw
        assert not set("\ufb00\ufb01\ufb02\ufb03\ufb04") & set(doc.text)


@pytest.mark.docker
@needs_image
def test_a_shell_escape_attempt_is_refused():
    source = ("\\documentclass{article}\\begin{document}\n"
              "\\immediate\\write18{echo pwned > /work/pwn.txt}\\input{pwn.txt}\n\\end{document}\n")
    for template in (L.builtin_template("cv"), L.builtin_template("letter")):
        out = L.compile_one(source, template, "evil", L.DEFAULT_IMAGE, 60)
        assert not out.ok
        assert "pwn.txt" in "\n".join(out.errors) and "pwned" not in out.text


@pytest.mark.docker
@needs_image
def test_a_compile_error_comes_back_as_latexs_own_lines():
    out = L.compile_one("\\documentclass{article}\\begin{document}\\fooo bar\\end{document}\n",
                        L.builtin_template("letter"), "bad", L.DEFAULT_IMAGE, 60)
    assert not out.ok
    assert out.errors[0].startswith("! Undefined control sequence.") and "\\fooo" in out.errors[0]
    assert "! Undefined control sequence." in out.failure_report()


@pytest.mark.docker
@needs_image
def test_a_compile_that_never_ends_is_stopped():
    out = L.compile_one("\\documentclass{article}\\begin{document}\\def\\a{\\a}\\a\\end{document}\n",
                        L.builtin_template("letter"), "loop", L.DEFAULT_IMAGE, 5)
    assert out.timed_out and not out.ok and out.pdf is None


@pytest.mark.docker
@needs_image
def test_just_the_tools_run_on_an_existing_pdf(documents):
    out = L.inspect_pdf(documents.cover.pdf)
    assert out.pages == 1 and "Dear Hiring Manager," in out.text and out.ok is False and out.errors == ()


@pytest.mark.docker
@needs_image
def test_the_registration_check_passes_for_the_shipped_templates_and_fails_for_a_wrong_limit(tmp_path):
    for kind in ("cv", "letter"):
        assert L.verify_template(L.builtin_template(kind)).pages == L.KINDS[kind][1]
    folder = tmp_path / "wrong"
    shutil.copytree(TEMPLATES / "letter", folder)
    manifest = json.loads((folder / "template.json").read_text())
    (folder / "template.json").write_text(json.dumps({**manifest, "page_limit": 2}))
    with pytest.raises(L.TemplateError, match="1 pages; its page_limit is 2"):
        L.verify_template(L.load_template(folder))


@pytest.mark.docker
@needs_image
def test_a_pdflatex_template_still_compiles_in_the_image(tmp_path):
    folder = make_folder(tmp_path, compile="pdflatex", page_limit=1)
    (folder / "sample.tex").write_text("\\documentclass{article}\\begin{document}Hello fi\\end{document}\n")
    assert L.verify_template(L.load_template(folder)).pages == 1


def run_in_container(tmp_path, code):
    argv = L.docker_argv(L.DEFAULT_IMAGE, tmp_path, "latexrun-probe-" + tmp_path.name, {})
    argv = argv[:argv.index(L.DEFAULT_IMAGE) + 1] + ["python3", "-I", "-c", code]
    return subprocess.run(argv, capture_output=True, text=True, timeout=60)


@pytest.mark.docker
@needs_image
def test_the_container_has_no_network_and_a_read_only_root_and_runs_as_uid_1000(tmp_path):
    tmp_path.chmod(0o755)
    code = ("import os, socket, sys\n"
            "r = []\n"
            "try:\n socket.create_connection(('1.1.1.1', 443), 3); r.append('network')\nexcept OSError: pass\n"
            "try:\n open('/usr/x', 'w'); r.append('writable-root')\nexcept OSError: pass\n"
            "try:\n open('/in/x', 'w'); r.append('writable-input')\nexcept OSError: pass\n"
            "print(os.getuid(), r)\n")
    done = run_in_container(tmp_path, code)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "1000 []"


@pytest.mark.docker
@needs_image
def test_a_missing_custom_include_blocks_registration(tmp_path):
    folder = make_folder(tmp_path, page_limit=1)
    (folder / "sample.tex").write_text("\\documentclass{mine}\\begin{document}Hi\\end{document}\n")
    with pytest.raises(L.TemplateError, match="mine.cls' not found"):
        L.verify_template(L.load_template(folder))
