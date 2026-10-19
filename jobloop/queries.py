"""queries.py: plan the searches from the profile's `## Search queries`, for the person to approve before any request.

    profile (queries by function and language, places) + boards ─► the (board, query, location) runs ─► printed

What it promises:
- Pure planning, no I/O: `plan_queries` reads text and returns runs; `main` only prints them. Nothing is requested here.
- Queries are organised by FUNCTION and written in every declared language. A board gets the queries of the languages it
  works in: a Danish board gets the Danish function queries, and a board for a language the person does not declare is not
  planned at all (the person could not work in its postings).
- Bounded: the default is the person's top 3 functions (the order they wrote them, `Adjacent roles` last), `broad` takes
  all of them, a focus puts the matching function first and adds at most 3 custom queries; at most `PER_BOARD` runs a
  board, at most `PLACES` places a query. Borderline and too-far places are never searched.
- Fail closed: an undeclared language, a language with no queries, an unknown board, or a query a board would refuse (empty,
  over 200 characters, starting with `-`) is an error before anything is planned.
"""
from __future__ import annotations

import argparse
import shlex
from dataclasses import dataclass
from pathlib import Path

from jobloop import profile as P, search
from jobloop.boards.contract import BoardError, Query
from jobloop.term import GREEN, RED, YELLOW, say
from jobloop.text import flat

HARNESS = False
TOP_CATEGORIES = 3
PER_BOARD = 12
PLACES = 2
FOCUS_QUERIES = 3
BROAD = "broad"
BOARD_LANGUAGES: dict[str, tuple[str, ...] | None] = {          # None: an international board, any declared language
    "freehire": None, "linkedin": None,
    "jobindex": ("Danish",), "jobnet": ("Danish",), "jobdanmark": ("Danish",), "jobbank": ("Danish",)}


@dataclass(frozen=True)
class Run:
    """One search to make: the board, the words, the place, and what the plan knew about why."""
    board: str
    query: str
    location: str
    recency_days: int | None
    language: str
    function: str

    def as_query(self, limit: int = 20) -> Query:
        return Query(self.query, self.location, self.recency_days, limit)


def board_languages(board: str, wanted: tuple[str, ...]) -> tuple[str, ...]:
    """The wanted (declared) languages this board searches in; empty when it works in none of them."""
    if board not in BOARD_LANGUAGES:
        raise ValueError(f"I do not know which language(s) the board '{board}' works in, so I cannot plan for it: "
                         f"known boards are {', '.join(BOARD_LANGUAGES)}")
    spoken = BOARD_LANGUAGES[board]
    return wanted if spoken is None else tuple(w for w in wanted if flat(w) in {flat(s) for s in spoken})


def _declared(profile: str, languages: tuple[str, ...] | None) -> tuple[str, ...]:
    declared = list(P.parse_languages(profile))
    if languages is None:
        return tuple(declared)
    byname = {flat(d): d for d in declared}
    unknown = [l for l in languages if flat(l) not in byname]
    if unknown:
        raise ValueError(f"the profile does not declare {', '.join(unknown)}: queries are planned only for the languages in "
                         f"its Languages table ({', '.join(declared)})")
    return tuple(byname[flat(l)] for l in languages)


def _functions(profile: str, wanted: tuple[str, ...]) -> tuple[dict[str, dict[str, tuple[str, ...]]], list[str]]:
    """Queries by declared language and the function labels in the person's priority order (`Adjacent roles` last)."""
    written = {flat(l): fs for l, fs in P.parse_search_queries(profile).items()}
    by_language = {}
    for language in wanted:
        if not written.get(flat(language)):
            raise ValueError(f"the profile has no '### {language}' search queries: add them (`--section search`)")
        by_language[language] = written[flat(language)]
    order = list(by_language[wanted[0]]) if wanted else []
    return by_language, [f for f in order if f != P.ADJACENT] + [f for f in order if f == P.ADJACENT]


def _focused(order: list[str], by_language: dict[str, dict[str, tuple[str, ...]]], focus: str | None,
             top: int) -> tuple[list[str], str | None]:
    """The functions to run and the one the focus names (by its label, else by a query that holds it). `broad` runs all
    functions; a named function goes first."""
    if focus is None:
        return order[:top], None
    if flat(focus) == BROAD:
        return order, None
    named = next((f for f in order if flat(focus) in flat(f)), None) or next(
        (f for f in order if any(flat(focus) in flat(q) for qs in by_language.values() for q in qs.get(f, ()))), None)
    return ([named] + [f for f in order if f != named])[:top] if named else order[:top], named


def _places(profile: str) -> tuple[str, ...]:
    settings = P.parse_settings(profile)
    return (settings.ideal + settings.acceptable)[:PLACES] or ("",)


def _valid(text: str) -> str:
    try:
        return Query(text).text
    except BoardError as error:
        raise ValueError(str(error)) from None


def plan_queries(profile: str, boards: list[str] | tuple[str, ...], languages: tuple[str, ...] | None = None,
                 recency: int | None = 14, focus: str | None = None, *, top: int = TOP_CATEGORIES,
                 per_board: int = PER_BOARD) -> tuple[Run, ...]:
    """The runs for these boards: top functions (all with focus `broad`), every language the board works in, the places
    the person calls ideal or acceptable, a focus's custom queries first. Several title variants of one function come
    round by round, so every function gets its first variant before any gets its second."""
    focus = " ".join(focus.split()) if focus is not None else None
    if focus == "":
        raise ValueError("a focus needs words: name a function or a skill, or `broad`")
    if top < 1 or per_board < 1:
        raise ValueError("top and per_board must be at least 1")
    wanted = _declared(profile, languages)
    by_language, order = _functions(profile, wanted)
    chosen, named = _focused(order, by_language, focus, top)
    places = _places(profile)
    runs: list[Run] = []
    for board in boards:
        spoken = board_languages(board, wanted)
        items: list[tuple[str, str, str]] = []                                        # (function, language, query)
        if focus and flat(focus) != BROAD:
            for language in spoken:
                variants = by_language[language].get(named or (chosen[0] if chosen else ""), ())
                custom = [focus, *(v if flat(focus) in flat(v) else f"{focus} {v}" for v in variants)]
                items += [(f"focus: {focus}", language, q) for q in custom[:FOCUS_QUERIES]]
        rounds = max((len(by_language[l].get(f, ())) for l in spoken for f in chosen), default=0)
        for round_ in range(rounds):
            for function in chosen:
                for language in spoken:
                    variants = by_language[language].get(function, ())
                    if round_ < len(variants):
                        items.append((function, language, variants[round_]))
        mine: dict[tuple[str, str], Run] = {}
        for function, language, text in items:
            for place in places:
                mine.setdefault((flat(_valid(text)), place), Run(board, _valid(text), place, recency, language, function))
        runs += list(mine.values())[:per_board]
    return tuple(runs)


# ── the command: print the plan, make no request ──
def _skipped(boards: list[str], wanted: tuple[str, ...]) -> list[str]:
    return [f"{b}: not planned, it works in {', '.join(BOARD_LANGUAGES[b])} and your profile does not declare it"
            for b in boards if not board_languages(b, wanted)]


def _off(me: Path, wanted: tuple[str, ...]) -> list[str]:
    return [f"{b}: off, it works in {', '.join(BOARD_LANGUAGES[b] or ('any language',))}; switch it on with `search boards "
            f"enable {b}` if you want it" for b in BOARD_LANGUAGES
            if b not in search.enabled_boards(me) and board_languages(b, wanted)]


def main(args: argparse.Namespace) -> int:
    """Print the planned searches as commands to run, and why boards were left out. No request is made. 0 if there is a
    plan, 1 when there is nothing to plan or the profile cannot be planned from."""
    me = Path(args.me)
    try:
        if not (me / "profile.md").is_file():
            raise ValueError(f"there is no {me / 'profile.md'}: build your profile first")
        profile = (me / "profile.md").read_text()
        boards = list(args.board) or search.enabled_boards(me)
        languages = tuple(getattr(args, "language", None) or ()) or None
        wanted = _declared(profile, languages)
        runs = plan_queries(profile, boards, languages, args.recency_days, getattr(args, "focus", None),
                            per_board=getattr(args, "per_board", None) or PER_BOARD)
        notes = _skipped(boards, wanted) + _off(me, wanted)
    except (ValueError, P.ProfileError) as error:
        say(str(error), RED)
        return 1
    for board in dict.fromkeys(r.board for r in runs):
        say(f"{board} ({', '.join(dict.fromkeys(r.language for r in runs if r.board == board))})", GREEN)
        for place in dict.fromkeys(r.location for r in runs if r.board == board):
            mine = [r for r in runs if r.board == board and r.location == place]
            for r in mine:
                say(f"  {r.function}: {r.query}")
            where = f" --location {shlex.quote(place)}" if place else ""
            days = f" --recency-days {mine[0].recency_days}" if mine[0].recency_days else ""
            say(f"  to run: search --board {board}{where}{days} "
                + " ".join(f"--query {shlex.quote(r.query)}" for r in mine))
    for note in notes:
        say(note, YELLOW)
    say(f"{len(runs)} search(es) planned; nothing has been requested. Run the commands above to go ahead.")
    return 0 if runs else 1
