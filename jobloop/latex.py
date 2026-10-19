"""LaTeX escaping and the lint for a drafted .tex body.

`escape` makes plain text safe to put in a document; `check` finds what would silently break one: an unescaped
percent (it deletes the rest of the line without an error), and unescaped & # _ ^ ~ $ { } or a stray backslash in text
positions, plus commands a drafted body must never carry (file access, shell escape). Legitimate commands, comment lines,
math, tabular cells and link targets are not flagged. Findings carry line numbers. `to_text` turns a .tex body into the
text a reader (or a word count) sees. Pure functions, no I/O.
"""
from __future__ import annotations

import re

from jobloop.style import locate

_ESCAPES = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{",
            "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
_TABLES = {"tabular", "tabular*", "tabularx", "array", "longtable", "align", "align*", "aligned", "matrix", "cases"}
_DEFINING = re.compile(r"\\(?:re)?newcommand|\\providecommand|\\def\b|\\(?:re)?newenvironment")
_ALWAYS_FORBIDDEN = ("write", "immediate", "openout", "openin", "read", "catcode", "csname", "directlua", "luaexec",
                     "newwrite", "newread", "ShellEscape", "special")
_BODY_FORBIDDEN = ("input", "include", "includeonly", "usepackage", "documentclass")
_ARGS = {"vspace": 1, "hspace": 1, "needspace": 1, "enlargethispage": 1, "fontsize": 2,
         "definecolor": 3, "colorlet": 2, "color": 1, "setstretch": 1, "linespread": 1, "pagestyle": 1,
         "thispagestyle": 1}
_PRIVATE = {c: chr(0xE000 + i) for i, c in enumerate("&%$#_{}")}


def escape(text: str) -> str:
    """Plain text made safe for a document: every LaTeX special character written as its escape."""
    return re.sub(r"[\\&%$#_{}~^]", lambda m: _ESCAPES[m.group()], text)


def bullet(text: str) -> str:
    """An \\item for plain text; a text that starts with [ is braced, or \\item would swallow it as a label."""
    body = escape(text.strip())
    return r"\item " + ("{" + body + "}" if body.startswith("[") else body)


def _body(tex: str) -> tuple[str, int]:
    """The text after \\begin{document} (all of it when there is none) and the lines skipped before it."""
    start = tex.find(r"\begin{document}")
    if start < 0:
        return tex, 0
    start += len(r"\begin{document}")
    end = tex.find(r"\end{document}", start)
    return tex[start:end if end >= 0 else len(tex)], tex.count("\n", 0, start)


def specials(tex: str) -> list[str]:
    """Unescaped special characters in text positions of the body, with line numbers."""
    body, skipped = _body(tex)
    found, envs, braces, math = [], [], [], None

    def flag(n: int, line: str, col: int, what: str) -> None:
        found.append(f'line {n}: {what} in "{line.strip()[max(0, col - 25):col + 25]}"')

    for n, line in enumerate(body.split("\n"), skipped + 1):
        if line.lstrip().startswith("%"):
            continue
        defining = bool(_DEFINING.search(line))
        i = 0
        while i < len(line):
            c = line[i]
            if c == "\\":
                m = re.match(r"\\([A-Za-z@]+)\*?", line[i:])
                if m:
                    i += m.end()
                    name = m.group(1)
                    if name == "item" and re.match(r"\s*\[", line[i:]):
                        flag(n, line, i, r"an \item whose text starts with [ (it is swallowed as a label; write \item {[...]})")
                    arg = re.match(r"\{([^{}]*)\}", line[i:])
                    if name in ("begin", "end") and arg:
                        if name == "begin":
                            envs.append(arg.group(1))
                        elif envs:
                            envs.pop()
                    if name in ("begin", "end", "href", "url") and arg:
                        i += arg.end()
                    continue
                nxt = line[i + 1:i + 2]
                if nxt == "" or nxt.isdigit():
                    flag(n, line, i, "a backslash that starts no command (write \\textbackslash{})")
                elif nxt in "()":
                    math = n if nxt == "(" else None
                i += 2
                continue
            if c == "%":
                rest, before = line[i + 1:].strip(), line[:i]
                if (before.strip() and rest) or re.search(r"\d\s*$", before):
                    flag(n, line, i, r"unescaped % (it deletes the rest of the line; write \% or put the comment on its own line)")
                break
            if c == "{":
                braces.append(n)
            elif c == "}":
                if braces:
                    braces.pop()
                else:
                    flag(n, line, i, "a closing } with no opening {")
            elif c == "$":
                if math is None and line[i + 1:i + 2].isdigit():
                    flag(n, line, i, r"unescaped $ (write \$)")
                else:
                    math = None if math is not None else n
            elif math is None:
                tabular = any(e in _TABLES for e in envs)
                if c == "&" and not tabular:
                    flag(n, line, i, r"unescaped & (write \&)")
                elif c == "#" and not defining:
                    flag(n, line, i, r"unescaped # (write \#)")
                elif c in "_^" and not defining:
                    flag(n, line, i, rf"unescaped {c} outside math (write \{c if c == '_' else 'textasciicircum{}'})")
                elif c == "~" and (line[i - 1:i] in ("", " ") or line[i + 1:i + 2] in ("", " ")):
                    flag(n, line, i, r"unescaped ~ (write \textasciitilde{})")
            i += 1
    found += [f"line {n}: an opening {{ is never closed" for n in braces]
    if math is not None:
        found.append(f"line {math}: math opened with $ is never closed (a literal dollar is written \\$)")
    return sorted(found, key=lambda s: int(s.split(":")[0].split()[1]))


def unsafe(tex: str) -> list[str]:
    """Commands that read or write files, run a shell or redefine the engine: never in a drafted document."""
    body_start = max(tex.find(r"\begin{document}"), 0)
    found = []
    for name in _ALWAYS_FORBIDDEN + _BODY_FORBIDDEN:
        for m in re.finditer(r"\\" + name + r"(?![A-Za-z])", tex):
            if name in _ALWAYS_FORBIDDEN or m.start() >= body_start:
                found.append(f"{locate(tex, m.start(), m.end())} uses \\{name}, which a drafted document may not contain")
    return found


_SHRINKING = (
    (r"\\(?:new)?geometry\b", "changes the page geometry"),
    (r"\\enlargethispage\b", "enlarges the page instead of cutting content"),
)
_NEGATIVE_VSPACE = re.compile(r"\\vspace\*?\{\s*-")
_BODY_LENGTHS = re.compile(r"\\setlength\{\\(?:textheight|textwidth|topmargin|oddsidemargin|evensidemargin|hoffset|voffset|parskip|"
                           r"baselineskip|footskip|headheight|headsep)\}")
_STRETCH = re.compile(r"\\(?:linespread|setstretch|renewcommand\{\\baselinestretch\})\{\s*([0-9.]+)\s*\}")
_ENGLISH_HEADINGS = ("profile", "core competencies", "professional experience", "education", "languages", "publications",
                     "honors and awards", "honours and awards", "references")


def _uncommented(tex: str) -> str:
    """The same text with every comment blanked out (same length, so positions still line up)."""
    return re.sub(r"(?<!\\)%[^\n]*", lambda m: " " * len(m.group()), tex)


def layout_rules(tex: str) -> list[str]:
    """What a drafted document may never do to make itself fit (shrink or enlarge the page, squeeze spacing), and the layout
    mistakes the CV guidance names: a \\vspace between \\item lines, \\needspace in front of a \\section, an empty \\address,
    and a \\cventry date written with `--` or a Unicode dash. Fit is reached by cutting content, so a draft that tries
    another way is sent back."""
    found = []
    tex = _uncommented(tex)
    for pattern, why in _SHRINKING:
        found += [f"{locate(tex, m.start(), m.end())} {why}: cut content to fit instead" for m in re.finditer(pattern, tex)]
    body, _ = _body(tex)
    start = tex.find(body) if body else 0
    for m in _NEGATIVE_VSPACE.finditer(body):
        found.append(f"{locate(tex, start + m.start(), start + m.end())} uses a negative \\vspace: cut content to fit instead")
    for m in _BODY_LENGTHS.finditer(body):
        found.append(f"{locate(tex, start + m.start(), start + m.end())} resets a page length: cut content to fit instead")
    for m in _STRETCH.finditer(body):
        if float(m.group(1)) < 1:
            found.append(f"{locate(tex, start + m.start(), start + m.end())} squeezes the line spacing below 1: cut content to fit instead")
    for m in re.finditer(r"\\begin\{(itemize|enumerate)\}(.*?)\\end\{\1\}", tex, re.S):
        gap = re.search(r"\\item\b.*?(\\vspace\b)", m.group(2), re.S)
        if gap:
            at = m.start(2) + gap.start(1)
            found.append(f"{locate(tex, at, at + 7)} puts \\vspace inside a list: it makes an oversized gap between items; remove it")
    for m in re.finditer(r"\\needspace\{[^{}]*\}\s*(?:%[^\n]*\n\s*)*\\section\b", tex):
        found.append(f"{locate(tex, m.start(), m.start() + 10)} sits before a \\section: use \\needspace before an entry only")
    for m in re.finditer(r"\\address\s*\{\s*\}", tex):
        found.append(f"{locate(tex, m.start(), m.end())} is an empty address line, which does not compile: delete the line")
    for m in re.finditer(r"\\cventry\{([^{}]*)\}", tex):
        if re.search(r"--|[\u2010-\u2015\u2212]", m.group(1)):
            found.append(f"{locate(tex, m.start(), m.end())} has a date range that is not one ASCII hyphen: write 2021 - 2023")
    return found


def cv_rules(tex: str, language: str = "en") -> list[str]:
    """The CV-only structure rules: a bold-labelled competency list has 5 to 7 entries, and the section headings are
    translated whenever the CV is not in English."""
    found = []
    tex = _uncommented(tex)
    for m in re.finditer(r"\\begin\{itemize\}(.*?)\\end\{itemize\}", tex, re.S):
        items = [i for i in re.split(r"\\item\b", m.group(1))[1:]]
        if items and all(i.lstrip().startswith(r"\textbf{") for i in items) and not 5 <= len(items) <= 7:
            found.append(f"{locate(tex, m.start(), m.start() + 20)} the competency list has {len(items)} bold-labelled items; it needs 5 to 7")
    if language != "en":
        for m in re.finditer(r"\\section\*?\{([^{}]*)\}", tex):
            if m.group(1).strip().casefold() in _ENGLISH_HEADINGS:
                found.append(f"{locate(tex, m.start(), m.end())} is an English heading in a CV written in {language}: translate it")
    return found


def check(tex: str) -> list[str]:
    """Every escaping, safety and layout-rule finding in a drafted .tex body."""
    return specials(tex) + unsafe(tex) + layout_rules(tex)


def to_text(tex: str, keep_links: bool = False) -> str:
    """The text of a .tex body as a reader sees it: no comments, layout or commands; \\item becomes '- '; adjacent
    arguments (as in \\entry{a}{b}{c}) are separated by ' | '; escapes are undone; links keep their address on request."""
    body, _ = _body(tex)
    body = re.sub(r"(?<!\\)%.*", "", body)
    body = re.sub(r"\\(?:setlength|addtolength)\*?\s*(?:\{[^{}]*\}|\\[A-Za-z@]+)\s*\{[^{}]*\}", "", body)
    for name, n in _ARGS.items():
        body = re.sub(r"\\" + name + r"\*?(?:\{[^{}]*\}){%d}" % n, "", body)
    body = re.sub(r"\\href\{([^{}]*)\}\{([^{}]*)\}", (r"\2 <\1>" if keep_links else r"\2"), body)
    body = re.sub(r"\\url\{([^{}]*)\}", r"\1", body)
    body = re.sub(r"\\([&%$#_{}])", lambda m: _PRIVATE[m.group(1)], body)
    body = re.sub(r"\\\\(?:\[[^\]]*\])?", "\n", body)
    body = re.sub(r"\\item\b\s*", "- ", body)
    body = re.sub(r"\\(?:begin|end)\{[^{}]*\}(?:\[[^\]]*\])?", "", body)
    body = re.sub(r"\}(?=\{)", " | ", body)
    body = re.sub(r"\\[A-Za-z@]+\*?", " ", body)
    body = body.replace("{", "").replace("}", "").replace("~", " ")
    for plain, private in _PRIVATE.items():
        body = body.replace(private, plain)
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in body.split("\n"))
