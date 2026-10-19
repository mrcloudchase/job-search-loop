"""Compile a CV and a cover letter in a throwaway container and inspect the PDFs; the loop's only LaTeX runner.

Promises: documents are compiled with no network, a read-only root, size-limited tmpfs, uid 1000, no capabilities, a
time limit and shell escape off. The engine and source extension come only from a fixed allowlist, never from a free-form
command. A failed compile returns LaTeX's own error lines word for word. Every PDF comes back with the text of
pdfinfo, pdffonts, pdftotext (default layout and raw) and pdftotext -bbox-layout, so later checks never run a tool themselves.
"""
from __future__ import annotations

import base64
import json
import re
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import layout, pdfcheck

DEFAULT_IMAGE = "job-search-check:2"
TEMPLATES_DIR = Path(__file__).parent / "templates"
ENGINES = ("lualatex", "xelatex", "pdflatex")
SOURCE_EXTENSIONS = (".tex",)
KINDS = {"cv": ("lualatex", 2), "letter": ("xelatex", 1)}
LOG_TAIL_LINES = 40
MANIFEST = "template.json"
_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


class TemplateError(ValueError):
    """A template folder or a compile request that the runner refuses."""


def compile_argv(engine: str, source: str) -> list[str]:
    """The only compile command: an allowlisted engine, nonstop, shell escape off, on one source file name."""
    if engine not in ENGINES:
        raise TemplateError(f"compile engine {engine!r} is not allowed; use one of {', '.join(ENGINES)}")
    if not _FILE.fullmatch(source) or Path(source).suffix not in SOURCE_EXTENSIONS:
        raise TemplateError(f"source file {source!r} must be a plain name ending in {', '.join(SOURCE_EXTENSIONS)}")
    return [engine, "-interaction=nonstopmode", "-no-shell-escape", source]


def extract_errors(log: str) -> tuple[str, ...]:
    """Each LaTeX error as it appears in the log: the '!' line through the 'l.<n>' source line and the line after it."""
    lines = log.splitlines()
    errors = []
    for i, line in enumerate(lines):
        if not line.startswith("!"):
            continue
        end = min(i + 12, len(lines))
        for j in range(i + 1, end):
            if lines[j].startswith("l."):
                end = min(j + 2, len(lines))
                break
        errors.append("\n".join(lines[i:end]))
    return tuple(errors)


def log_tail(log: str, lines: int = LOG_TAIL_LINES) -> str:
    return "\n".join(log.splitlines()[-lines:])


@dataclass(frozen=True)
class Template:
    """A template folder: a skeleton with placeholders, a filled sample, companion files and a declared toolchain."""

    name: str
    kind: str
    folder: Path
    source: str
    sample: str
    extension: str
    compile: str
    page_limit: int

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise TemplateError(f"template name {self.name!r} must be kebab-case (lower case words joined by hyphens)")
        if self.kind not in KINDS:
            raise TemplateError(f"template kind {self.kind!r} must be one of {', '.join(KINDS)}")
        if self.extension not in SOURCE_EXTENSIONS:
            raise TemplateError(f"source extension {self.extension!r} is not supported; use {', '.join(SOURCE_EXTENSIONS)}")
        compile_argv(self.compile, "x" + self.extension)
        if isinstance(self.page_limit, bool) or not isinstance(self.page_limit, int) or self.page_limit < 1:
            raise TemplateError(f"page_limit {self.page_limit!r} must be a whole number of at least 1")
        for label, file in (("source", self.source), ("sample", self.sample)):
            if not _FILE.fullmatch(file) or not file.endswith(self.extension):
                raise TemplateError(f"{label} {file!r} must be a plain file name ending in {self.extension}")
            if not (self.folder / file).is_file():
                raise TemplateError(f"{label} {file!r} is missing from {self.folder}")
        if self.source == self.sample:
            raise TemplateError("source and sample must be different files")
        for path in self._texts():
            if "fontspec" in path.read_text(encoding="utf-8", errors="replace") and self.compile == "pdflatex":
                raise TemplateError(f"{path.name} uses fontspec, which needs xelatex or lualatex, not pdflatex")

    def _texts(self) -> list[Path]:
        return [p for p in self.folder.rglob("*") if p.is_file() and p.suffix in (".tex", ".cls", ".sty")]

    def support_files(self) -> dict[str, bytes]:
        """Companion files (class, styles, fonts) compiled alongside the document; symlinks are refused."""
        files = {}
        for path in sorted(self.folder.rglob("*")):
            if path.is_symlink():
                raise TemplateError(f"{path} is a symlink; templates must hold plain files")
            rel = path.relative_to(self.folder).as_posix()
            if path.is_file() and rel not in (MANIFEST, self.source, self.sample):
                files[rel] = path.read_bytes()
        return files


def load_template(folder: Path) -> Template:
    """Read and validate a template folder's template.json; refuse anything off the allowlist."""
    manifest = Path(folder) / MANIFEST
    if not manifest.is_file():
        raise TemplateError(f"{folder} has no {MANIFEST}; see docs/templates.md")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateError(f"{manifest} is not valid JSON: {exc}") from exc
    keys = {"name", "kind", "source", "sample", "extension", "compile", "page_limit"}
    if not isinstance(data, dict) or set(data) != keys:
        raise TemplateError(f"{manifest} must hold exactly these keys: {', '.join(sorted(keys))}")
    return Template(folder=Path(folder), **data)


def builtin_template(kind: str) -> Template:
    if kind not in KINDS:
        raise TemplateError(f"template kind {kind!r} must be one of {', '.join(KINDS)}")
    return load_template(TEMPLATES_DIR / kind)


@dataclass(frozen=True)
class Rendered:
    """One document after the container ran: the PDF (if any) and everything read from it."""

    name: str
    ok: bool
    pdf: bytes | None
    log_tail: str
    errors: tuple[str, ...]
    timed_out: bool
    pdfinfo: str
    text: str
    text_raw: str
    bbox: str
    fonts: str
    tool_errors: tuple[str, ...]

    @property
    def pages(self) -> int | None:
        match = re.search(r"^Pages:\s+(\d+)", self.pdfinfo, re.M)
        return int(match.group(1)) if match else None

    def failure_report(self) -> str:
        """What a drafter needs to fix a failed compile: LaTeX's own error lines, else the log tail."""
        if self.timed_out:
            return f"{self.name}: the compile ran out of time.\n{self.log_tail}"
        return f"{self.name}: the compile failed.\n" + ("\n\n".join(self.errors) if self.errors else self.log_tail)


@dataclass(frozen=True)
class Documents:
    cv: Rendered
    cover: Rendered


# The tool runs, as source so they run inside the container and the tests can run them too. Nothing is extracted from a
# path that is not a file; a tool that is missing or exits non-zero is "could not read the PDF" with its own words (stderr,
# else stdout, else "command failed"), never a traceback; every output is decoded as UTF-8 with replacement.
_TOOLS = r'''
def run_tools(pdf, commands, out):
    if not os.path.isfile(pdf):
        out["tool_errors"].append("pdf: " + pdf + " is not a file, so nothing was extracted")
        return
    for key, argv in commands.items():
        argv = [pdf if part == "{pdf}" else part for part in argv]
        try:
            done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as error:
            out["tools"][key] = ""
            out["tool_errors"].append(key + ": could not read the PDF: " + argv[0] + " did not run (" + type(error).__name__ + ")")
            continue
        out["tools"][key] = done.stdout.decode("utf-8", "replace")
        if done.returncode != 0:
            detail = (done.stderr.decode("utf-8", "replace").strip() or done.stdout.decode("utf-8", "replace").strip()
                      or "command failed")
            out["tools"][key] = ""
            out["tool_errors"].append(key + ": could not read the PDF: " + detail)
'''

# Runs inside the container: compile (two passes) or just inspect, then print one JSON object. The font indexes built
# into the image are copied to a writable home first, because lualatex needs to write beside them.
_INNER = r'''
import base64, json, os, shutil, subprocess, sys
''' + _TOOLS + r'''
spec = json.loads(sys.argv[1])
shutil.copytree("/in", "/work", dirs_exist_ok=True)
for kept in (".texlive2022", ".cache"):
    shutil.copytree("/home/checker/" + kept, "/tmp/home/" + kept)
os.chdir("/work")
out = {"ok": False, "timed_out": False, "log": "", "pdf": None, "tools": {}, "tool_errors": []}
pdf = spec["pdf"]
if spec["mode"] == "compile":
    ok = True
    for _ in range(2):
        try:
            done = subprocess.run(spec["argv"], stdin=subprocess.DEVNULL, capture_output=True, timeout=spec["timeout"])
        except subprocess.TimeoutExpired:
            out["timed_out"] = True
            ok = False
            break
        ok = ok and done.returncode == 0
        if not ok:
            break
    logfile = pdf[:-4] + ".log"
    if os.path.exists(logfile):
        out["log"] = open(logfile, encoding="utf-8", errors="replace").read()
    out["ok"] = ok and os.path.exists(pdf)
if os.path.isfile(pdf):
    out["pdf"] = base64.b64encode(open(pdf, "rb").read()).decode()
if spec["mode"] == "inspect" or out["ok"]:
    run_tools(pdf, spec["tools"], out)
print(json.dumps(out))
'''


def tool_commands() -> dict[str, list[str]]:
    """The extraction commands, in the order they run (the page count first), built from the argv the checks pin."""
    return {"pdfinfo": [*pdfcheck.PDFINFO_ARGV, "{pdf}"], "text": [*pdfcheck.TEXT_ARGV, "{pdf}", "-"],
            "text_raw": ["pdftotext", "-raw", "-enc", "UTF-8", "{pdf}", "-"], "bbox": [*layout.BBOX_ARGV, "{pdf}", "-"],
            "fonts": ["pdffonts", "{pdf}"]}


def docker_argv(image: str, indir: Path, name: str, spec: dict) -> list[str]:
    """The container's confinement, in one place: no network, read-only root, no capabilities, uid 1000."""
    tmpfs = "rw,nosuid,nodev,size={size}m,uid=1000,gid=1000,mode=700"
    return [
        "docker", "run", "--rm", "--name", name, "--network", "none", "--read-only", "--user", "1000:1000",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256", "--memory", "1g",
        "--tmpfs", "/work:" + tmpfs.format(size=128), "--tmpfs", "/tmp:" + tmpfs.format(size=64),
        "-v", f"{indir}:/in:ro", "-w", "/work", "-e", "HOME=/tmp/home",
        image, "python3", "-I", "-c", _INNER, json.dumps(spec),
    ]


def _run(image: str, files: dict[str, bytes], spec: dict, label: str, timeout: int) -> Rendered:
    with tempfile.TemporaryDirectory(prefix="latexrun-") as tmp:
        indir = Path(tmp)
        indir.chmod(0o755)
        for rel, data in files.items():
            if rel.startswith("/") or ".." in Path(rel).parts:
                raise TemplateError(f"file path {rel!r} escapes the work folder")
            target = indir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o644)
            for parent in target.parents:
                if parent == indir.parent:
                    break
                parent.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
        name = f"latexrun-{uuid.uuid4().hex[:12]}"
        argv = docker_argv(image, indir, name, spec)
        try:
            done = subprocess.run(argv, capture_output=True, timeout=2 * timeout + 120)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            return Rendered(label, False, None, "the container did not finish in time", (), True, "", "", "", "", "", ())
    if done.returncode != 0:
        raise RuntimeError(f"the check container failed to run ({done.returncode}): {done.stderr.decode(errors='replace')}")
    out = json.loads(done.stdout)
    tools = out["tools"]
    return Rendered(
        name=label, ok=out["ok"], pdf=base64.b64decode(out["pdf"]) if out["pdf"] else None,
        log_tail=log_tail(out["log"]), errors=extract_errors(out["log"]), timed_out=out["timed_out"],
        pdfinfo=tools.get("pdfinfo", ""), text=tools.get("text", ""), text_raw=tools.get("text_raw", ""),
        bbox=tools.get("bbox", ""), fonts=tools.get("fonts", ""), tool_errors=tuple(out["tool_errors"]))


def compile_one(source_text: str, template: Template, stem: str, image: str, timeout: int) -> Rendered:
    """Compile one document with its template's companion files and declared engine."""
    source = stem + template.extension
    files = template.support_files()
    if source in files:
        raise TemplateError(f"template {template.name} ships a file named {source}, which is the document's own name")
    files[source] = source_text.encode("utf-8")
    spec = {"mode": "compile", "argv": compile_argv(template.compile, source), "pdf": stem + ".pdf", "timeout": timeout,
            "tools": tool_commands()}
    return _run(image, files, spec, stem, timeout)


def compile_documents(cv_tex: str, cover_tex: str, image: str = DEFAULT_IMAGE, *, cv_template: Template | None = None,
                      cover_template: Template | None = None, timeout: int = 180) -> Documents:
    """Compile the CV (lualatex by default) and the cover letter (xelatex by default), each in its own container."""
    cv_template = cv_template or builtin_template("cv")
    cover_template = cover_template or builtin_template("letter")
    if cv_template.kind != "cv" or cover_template.kind != "letter":
        raise TemplateError("cv_template must be a cv template and cover_template a letter template")
    return Documents(cv=compile_one(cv_tex, cv_template, "cv", image, timeout),
                     cover=compile_one(cover_tex, cover_template, "cover", image, timeout))


def verify_template(template: Template, image: str = DEFAULT_IMAGE, *, timeout: int = 180) -> Rendered:
    """The registration check: the template's filled sample must compile cleanly to exactly its declared page limit."""
    sample = (template.folder / template.sample).read_text(encoding="utf-8")
    rendered = compile_one(sample, template, "sample", image, timeout)
    if not rendered.ok:
        raise TemplateError(f"template {template.name} does not compile.\n{rendered.failure_report()}")
    if rendered.pages != template.page_limit:
        raise TemplateError(
            f"template {template.name} sample has {rendered.pages} pages; its page_limit is {template.page_limit}")
    return rendered


def inspect_pdf(pdf: bytes, image: str = DEFAULT_IMAGE, *, timeout: int = 60) -> Rendered:
    """Run just the tools (pdfinfo, pdftotext, bbox) on an existing PDF, in the same confinement."""
    spec = {"mode": "inspect", "pdf": "doc.pdf", "timeout": timeout, "tools": tool_commands()}
    return _run(image, {"doc.pdf": pdf}, spec, "doc", timeout)
