"""salary.py: an optional salary benchmark from data the PERSON supplies. The loop ships no salary data and fetches none.

The data lives under me/ (default me/salary/benchmark.json or benchmark.csv) and only the matched company's rows ever leave
this module. Two formats, both read as UTF-8 (a BOM is tolerated):

JSON (benchmark.json):
    {"metadata": {"index_baseline": 100, "index_label": "Index", "source": "...", "description": "..."},
     "companies": [{"name": "Acme A/S", "city": "København",
                    "categories": {"engineers": {"count": 12, "index": 118.5},
                                   "managers": {"count": 3, "index": null}}}]}
  metadata is optional (baseline 100, label "Index"); baseline 0 means absolute amounts, shown without a percent.
  `count` is the number of people (an integer), `index` a number, a text such as "private" (shown as is), absent (shown "-")
  or null (the figure is withheld: shown "N/A*", and only then is the privacy footnote printed).

CSV (benchmark.csv, one row per company and category; extra columns are ignored):
    company,city,category,count,index
    Acme A/S,København,engineers,12,"118,5"
  Numbers may be localised: any whitespace is a thousands separator, when both "," and "." occur the LAST one is the decimal
  separator, a lone "," or "." followed by exactly three digits ("1,234") is ambiguous and skipped, never guessed, and a
  value that is not finite is never kept. A row without a category is the category "all_employees".

What it promises: invalid data is reported with its position and a pointer to this docstring, never a traceback; errors
(a lookup would crash or print a wrong figure) are separated from warnings (a duplicate company); a non-finite baseline is
rejected; a missing file is not an error (the step is skipped). A name matches by a scored ladder with a cutoff, never by
a guess below it. Not ported: the original's Excel reader and column guessing; the person supplies structured data.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


DEFAULT_BASELINE, DEFAULT_LABEL = 100, "Index"
MIN_SCORE = 30
DEFAULT_CATEGORY = "all_employees"
FOOTNOTE = "* N/A: too few employees to publish."
LEGAL_FORMS = ("a/s", "aps", "i/s", "p/s", "k/s", "ivs", "amba", "a.m.b.a", "gmbh", "ag", "ltd", "limited", "inc", "llc",
               "plc", "bv", "nv", "ab", "oy", "oyj", "sa")
NOISE_WORDS = ("group", "holding", "holdings", "denmark", "danmark", "scandinavia", "nordic", "europe")
NORDIC = (("ø", "o"), ("æ", "ae"), ("å", "aa"), ("ö", "o"), ("ä", "a"), ("ü", "u"), ("ß", "ss"))
_RESERVED = ("name", "city", "categories")
_COLUMNS = ("company", "city", "category", "count", "index")


class SalaryDataError(ValueError):
    """The person's data is not usable; the message says where and what to do."""


# ── numbers ─────────────────────────────────────────────────────────────────────────────────────
def finite(value: object) -> bool:
    """True for a real, finite number (not a bool). An int too large for a float is not finite."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _grouped(text: str, sep: str) -> str | None:
    groups = text.split(sep)
    return "".join(groups) if re.fullmatch(r"\d{1,3}", groups[0]) and all(re.fullmatch(r"\d{3}", g) for g in groups[1:]) else None


def parse_number(value: object) -> float | None:
    """A number from a cell, or None when it is not one, is ambiguous ("1,234") or is not finite. Never guesses."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if finite(value) else None
    if not isinstance(value, str):
        return None
    text = "".join(value.split())
    m = re.fullmatch(r"([+-]?)(\d[\d.,]*\d|\d)", text)
    if not m:
        return None
    sign, body = m.groups()
    seps = [c for c in body if c in ".,"]
    if not seps:
        digits = body
    elif "," in body and "." in body:
        last = max(body.rfind(","), body.rfind("."))
        whole, frac = body[:last], body[last + 1:]
        if body[last] in whole or not frac.isdigit() or (digits := _grouped(whole, "." if body[last] == "," else ",")) is None:
            return None
        digits += "." + frac
    elif len(seps) > 1:
        if len(set(seps)) > 1 or (digits := _grouped(body, seps[0])) is None:
            return None
    else:
        left, right = body.split(seps[0])
        if re.fullmatch(r"[1-9]\d{0,2}", left) and re.fullmatch(r"\d{3}", right):
            return None
        digits = f"{left}.{right}"
    number = float(sign + digits)
    return number if math.isfinite(number) else None


def _count(value: object) -> int | None:
    number = parse_number(value)
    return int(number) if number is not None and number == int(number) and number >= 0 else None


# ── validation ──────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Validation:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def _categories_of(entry: dict) -> dict:
    """The entry's categories; when `categories` is null or empty, its sibling objects stand in."""
    cats = entry.get("categories")
    if isinstance(cats, dict) and cats:
        return cats
    return {k: v for k, v in entry.items() if k not in _RESERVED and isinstance(v, dict)}


def validate(data: object) -> Validation:
    """Errors (a lookup would crash or print a wrong figure) and warnings (duplicates), each naming its position."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return Validation(("the top level must be an object with 'companies'",), ())
    meta = data.get("metadata")
    if meta is not None and not isinstance(meta, dict):
        errors.append("metadata must be an object when given")
    elif meta:
        if "index_baseline" in meta and not finite(meta["index_baseline"]):
            errors.append(f"metadata.index_baseline must be a finite number, got {meta['index_baseline']!r}")
        if "index_label" in meta and not isinstance(meta["index_label"], str):
            errors.append(f"metadata.index_label must be a string, got {meta['index_label']!r}")
    companies = data.get("companies")
    if not isinstance(companies, list):
        errors.append("companies must be a list")
        return Validation(tuple(errors), tuple(warnings))
    seen: dict[str, int] = {}
    for i, entry in enumerate(companies):
        where = f"companies[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{where}.name must be a non-blank string")
        else:
            key = unicodedata.normalize("NFC", " ".join(name.split())).casefold()
            if key in seen:
                warnings.append(f"{where} {name!r} duplicates companies[{seen[key]}] (compared without case)")
            seen.setdefault(key, i)
        if entry.get("city") is not None and not isinstance(entry["city"], str):
            errors.append(f"{where}.city must be a string when given")
        if entry.get("categories") is not None and not isinstance(entry["categories"], dict):
            errors.append(f"{where}.categories must be an object when given")
            continue
        for label, cat in _categories_of(entry).items():
            if not isinstance(cat, dict):
                errors.append(f"{where}.categories.{label} must be an object")
                continue
            if cat.get("count") is not None and not finite(cat["count"]):
                errors.append(f"{where}.categories.{label}.count must be a number, got {cat['count']!r}")
            if "index" in cat and cat["index"] is not None and not isinstance(cat["index"], str) and not finite(cat["index"]):
                errors.append(f"{where}.categories.{label}.index must be a finite number or text, got {cat['index']!r}")
    return Validation(tuple(errors), tuple(warnings))


# ── names ───────────────────────────────────────────────────────────────────────────────────────
def fold_accents(text: str) -> str:
    """Nordic letters first (ø to o, æ to ae, å to aa), then every other accent stripped: Mærsk is Maersk, Nestlé is Nestle."""
    text = unicodedata.normalize("NFC", text).casefold()
    for a, b in NORDIC:
        text = text.replace(a, b)
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c)[0] != "M")


def _stripped(name: str, legal: tuple[str, ...], noise: tuple[str, ...]) -> str:
    text = unicodedata.normalize("NFC", name).casefold().split(",")[0]
    text = re.sub(r"\([^)]*\)", " ", text)
    for form in sorted({f.replace(".", "") for f in legal}, key=len, reverse=True):
        text = re.sub(r"(?<!\w)" + r"\.?".join(map(re.escape, form)) + r"\.?(?!\w)", " ", text)
    for word in noise:
        text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", " ", text)
    return text


def normalize_company(name: str, legal: tuple[str, ...] = LEGAL_FORMS, noise: tuple[str, ...] = NOISE_WORDS) -> str:
    """Casefolded, NFC, legal forms and noise words, parentheticals and anything after a comma removed, then only letters,
    digits and combining marks of any script kept."""
    return "".join(c for c in _stripped(name, legal, noise) if c.isalnum() or unicodedata.category(c)[0] == "M")


def core_words(name: str, legal: tuple[str, ...] = LEGAL_FORMS, noise: tuple[str, ...] = NOISE_WORDS) -> list[str]:
    """The name's words longer than one character once legal forms are gone; "A/S" alone has none."""
    words = re.findall(r"(?:[^\W_]|[\u0300-\u036f])+", _stripped(name, legal, noise))
    return [w for w in words if len(w) > 1]


def _contains(a: str, b: str) -> int | None:
    """80-style containment with the short-token guard: a name of four characters or fewer inside one more than twice its length
    does not count. Returns the length ratio score part or None."""
    small, big = sorted((a, b), key=len)
    if not small or small not in big or (len(small) <= 4 and len(small) * 2 < len(big)):
        return None
    return int(9 * len(small) / len(big))


def match_score(query: str, entry: str, legal: tuple[str, ...] = LEGAL_FORMS, noise: tuple[str, ...] = NOISE_WORDS) -> int:
    """0 to 100: exact 100; substring 80 to 89; accent-folded equal 85; folded substring 75; shared words 30 to 70."""
    q, e = normalize_company(query, legal, noise), normalize_company(entry, legal, noise)
    if not q or not e:
        return 0
    if q == e:
        return 100
    if (ratio := _contains(q, e)) is not None:
        return 80 + ratio
    qf, ef = fold_accents(q), fold_accents(e)
    if qf == ef:
        return 85
    if _contains(qf, ef) is not None:
        return 75
    qw, ew = core_words(query, legal, noise), core_words(entry, legal, noise)
    efold = {fold_accents(w) for w in ew}
    shared = sum(w in ew or fold_accents(w) in efold for w in qw)
    if not qw or not ew or not shared:
        return 0
    return int(30 + shared / len(qw) * 40) if len(qw) > 1 else 70


@dataclass(frozen=True)
class Match:
    entry: dict
    score: int


def search(data: dict, query: str, city: str = "") -> list[Match]:
    """Every company scoring at least 30, best first then by name. An entry without a city never matches a city filter."""
    out = []
    for entry in data["companies"]:
        if city:
            have = entry.get("city")
            wanted = city.casefold()
            if not isinstance(have, str) or not (wanted in have.casefold() or fold_accents(city) in fold_accents(have)):
                continue
        if (score := match_score(query, entry["name"])) >= MIN_SCORE:
            out.append(Match(entry, score))
    return sorted(out, key=lambda m: (-m.score, m.entry["name"]))


# ── rendering ───────────────────────────────────────────────────────────────────────────────────
def _number(x: float) -> str:
    return str(int(x)) if x == int(x) else f"{x:.2f}".rstrip("0").rstrip(".")


def settings(data: dict) -> tuple[float, str]:
    """(baseline, label): defaults when metadata is absent, null or empty."""
    meta = data.get("metadata") or {}
    return meta.get("index_baseline", DEFAULT_BASELINE), meta.get("index_label", DEFAULT_LABEL)


def percent(index: float, baseline: float) -> str:
    """((index - baseline) / baseline) * 100, one decimal, explicit sign; nothing when the baseline is zero."""
    return f" ({(index - baseline) / baseline * 100:+.1f}%)" if baseline else ""


def render_entry(entry: dict, baseline: float, label: str) -> tuple[list[str], bool]:
    """The entry's lines, and whether one withheld its index. A category with neither count nor index renders nothing."""
    lines, suppressed = [f"{entry['name']}" + (f" ({entry['city']})" if entry.get("city") else "")], False
    for name, cat in _categories_of(entry).items():
        if cat.get("count") is None and "index" not in cat:
            continue
        count = "-" if cat.get("count") is None else _number(cat["count"])
        index = cat.get("index", "-") if "index" in cat else "-"
        if index is None:
            shown, suppressed = "N/A*", True
        elif isinstance(index, str):
            shown = index
        else:
            shown = f"{_number(index)}{percent(index, baseline)}"
        lines.append(f"  {name}: n={count}, {label} {shown}")
    return lines, suppressed


def render(data: dict, matches: list[Match]) -> str:
    """The matched companies only. The privacy footnote appears only when a rendered row withheld its index."""
    baseline, label = settings(data)
    out, any_suppressed = [], False
    for match in matches:
        lines, suppressed = render_entry(match.entry, baseline, label)
        out += [*lines, ""]
        any_suppressed |= suppressed
    if any_suppressed:
        out.append(FOOTNOTE)
    return "\n".join(out).rstrip() + "\n"


# ── loading and converting ──────────────────────────────────────────────────────────────────────
def ingest_csv(text: str, *, source: str = "", baseline: float = DEFAULT_BASELINE, label: str = DEFAULT_LABEL,
               description: str = "") -> dict:
    """The dataset in a long CSV (see the module docstring). Raises SalaryDataError when no company row is usable."""
    reader = csv.DictReader(io.StringIO(text))
    names = {(n or "").strip().casefold(): n for n in reader.fieldnames or []}
    if "company" not in names:
        raise SalaryDataError("the CSV needs a 'company' column (columns: company, city, category, count, index)")
    column = lambda row, key: row.get(names[key]) if key in names else None
    companies: dict[tuple[str, str], dict] = {}
    for row in reader:
        name = " ".join((column(row, "company") or "").split())
        if not name:
            continue
        city = " ".join((column(row, "city") or "").split())
        entry = companies.setdefault((name.casefold(), city.casefold()), {"name": name, "city": city or None, "categories": {}})
        cat = _csv_category(column(row, "count"), column(row, "index"), "count" in names, "index" in names)
        label_ = re.sub(r"[\s-]+", "_", " ".join((column(row, "category") or DEFAULT_CATEGORY).split())) or DEFAULT_CATEGORY
        if cat is not None:
            entry["categories"].setdefault(label_, cat)
    if not companies:
        raise SalaryDataError("No data could be parsed: no row has a company name")
    return {"metadata": {"source": source, "index_baseline": float(baseline), "index_label": label,
                         "description": description or f"{label} {_number(baseline)} = baseline"},
            "companies": list(companies.values())}


def _csv_category(count_cell, index_cell, has_count: bool, has_index: bool) -> dict | None:
    empty = lambda c: c is None or not str(c).strip()
    if has_count and has_index:
        if empty(count_cell) and empty(index_cell):
            return None
        return {"count": _count(count_cell), "index": parse_number(index_cell)}
    if has_count and not empty(count_cell) and (n := _count(count_cell)) is not None:
        return {"count": n}
    if has_index and not empty(index_cell) and (x := parse_number(index_cell)) is not None:
        return {"index": x}
    return None


def load(path: Path) -> dict | None:
    """The person's data, or None when the file is not there (the step is optional). Invalid data raises SalaryDataError."""
    path = Path(path)
    if not path.is_file():
        return None
    text = path.read_bytes().decode("utf-8-sig")
    if path.suffix.lower() == ".csv":
        return ingest_csv(text, source=path.stem)
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise SalaryDataError(f"{path}: invalid JSON at line {error.lineno} column {error.colno}: {error.msg}. "
                              "The format is described at the top of jobloop/salary.py.") from None


def find_data(me: Path) -> Path | None:
    return next((p for p in (Path(me) / "salary" / "benchmark.json", Path(me) / "salary" / "benchmark.csv") if p.is_file()), None)


@dataclass(frozen=True)
class Benchmark:
    text: str | None                              # None: no benchmark (no data, bad data, or no match)
    note: str


def benchmark(me: Path, company: str, city: str = "", path: Path | None = None) -> Benchmark:
    """The optional step as the loop calls it: never raises, and a problem is a note, not a failure."""
    path = Path(path) if path else find_data(me)
    if path is None or not path.is_file():
        return Benchmark(None, "no salary data supplied; skipped")
    try:
        data = load(path)
        result = validate(data)
    except SalaryDataError as error:
        return Benchmark(None, f"salary data skipped: {error}")
    if result.errors:
        return Benchmark(None, "salary data skipped: " + "; ".join(result.errors))
    matches = search(data, company, city)
    if not matches:
        return Benchmark(None, f"no benchmark found for {company!r}" + (f" in {city!r}" if city else "") + "; try a shorter name")
    return Benchmark(render(data, matches), "")
