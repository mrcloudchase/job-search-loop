# Templates, fonts and the check image

The loop drafts a CV and a cover letter into two templates it ships, compiles them in a throwaway container and inspects
the PDFs. Everything here is written for this project; nothing is taken from another project's templates.

## The two templates

| | CV | Cover letter |
|---|---|---|
| Folder | `jobloop/templates/cv/` | `jobloop/templates/letter/` |
| Built on | CTAN `moderncv`, banking style, blue, 11pt, A4, sans | our own class `jobletter.cls` (fontspec) |
| Engine | `lualatex` | `xelatex` |
| Page limit | exactly 2 | exactly 1 |
| Fonts | Latin Modern (TeX Live) | Lato Light for the name, Raleway Medium and Bold for the text |

Each folder holds `template.json` (the declaration), `template.tex` (the skeleton a drafter fills: every `[[LIKE THIS]]`
is a placeholder and the comment lines at the top are the drafter's rules) and `sample.tex` (the same skeleton filled
with fictional content, which proves the template compiles to its page limit).

The CV skeleton has these sections in this order: Profile, Core Competencies, Professional Experience, Education,
Languages, Publications, Honors and Awards, References. For a specialist role the drafter moves Education before
Experience. Headings are translated when the CV language is not English. Contact labels are plain text and bullets are
plain bullets, so the text layer of the PDF reads as text.

The letter has: name header, contact line, date with a place for the posting's reference id (`\jlreference`, which is
the full label and id in the posting's language, or empty), salutation, opening, bullet list (`letterbullets`, 3 to 5
bullets), company connection, personal fit, closing line, sign-off. The class prints no labels of its own, so a letter in
any language needs no change to the class. Bullets can only be written inside `letterbullets`, which sets the body font
itself.

Known limit: Lato stores its hyphen as U+2010, so a hyphenated name in the 40pt header extracts with that character.
Contact line, date and reference id use Raleway, whose hyphen is a plain hyphen.

## Running them: `jobloop/latexrun.py`

`compile_documents(cv_tex, cover_tex, image)` compiles both documents, each in its own container started with no
network, a read-only root, a size-limited tmpfs for work, uid 1000, all capabilities dropped, no new privileges, a
memory and process limit and a time limit. The compile is run twice (references settle), with `-interaction=nonstopmode`
and `-no-shell-escape`; a `\write18` is refused. For each document it returns a `Rendered`: the PDF bytes, `ok`, the log
tail, LaTeX's own error lines word for word (the `!` line through the `l.<n>` line and the one after it), whether it timed
out, and the text of `pdfinfo`, `pdffonts`, `pdftotext` (default layout and `-raw`) and `pdftotext -bbox-layout`.
`inspect_pdf(pdf, image)` runs just the tools on an existing PDF.

## Your own template

A person's template is a folder, handed to the loop by path. There is no registry and nothing is patched into a guidance
file. The folder holds:

- `template.json` with exactly these keys: `name` (kebab-case), `kind` (`cv` or `letter`), `source` (skeleton file
  name), `sample` (filled sample file name), `extension` (`.tex`), `compile` (`lualatex`, `xelatex` or `pdflatex`),
  `page_limit` (whole number, 2 for a CV and 1 for a letter by convention);
- the skeleton and the sample, as plain files ending in the extension;
- companion files (class, style, images, fonts), as plain files; symlinks are refused.

`load_template(folder)` validates all of it and refuses anything off the allowlist: another extension, another command,
a free-form command string, a file name with a path in it, or a template that uses fontspec but declares pdflatex.
`verify_template(template, image)` is the registration check: the sample must compile cleanly and to exactly
`page_limit` pages, or the folder is refused with LaTeX's error. Compile and the sample are always dummy data in a
throwaway container, so nothing is left behind. Write skeleton placeholders as `[[TOKENS]]` and never put personal data in
a template folder.

To use fonts of your own in a template, put the font files in the folder and load them by relative path; the image
installs only the fonts listed below.

## The check image

`check/Dockerfile` builds `job-search-check:2` from `debian:bookworm-slim`: TeX Live (latex-base, recommended, extra for
moderncv, luatex, xetex, fonts-recommended, fonts-extra for the fontawesome5 package that moderncv loads, cm-super,
lmodern), poppler-utils, python3, the fonts below, running as uid 1000. It is large (about 3.6 GB) because moderncv
needs fontawesome5, which lives in TeX Live's fonts-extra. pdflatex stays available, so the simple templates in
`fixtures/` still compile in it.

```
docker build -t job-search-check:2 check
```

On a network that re-signs TLS, put the proxy's CA certificates (`*.crt`) in `check/extra-ca/` first. The Dockerfile
installs them before any https step; on a normal network leave the directory empty. Its contents are gitignored except
`.gitkeep`.

## Fonts

Fetched at image build from upstream, checked against a pinned SHA-256 (an upstream change fails the build), used
unmodified, never committed. Their licences are saved in `/usr/local/share/fonts/jobloop/` in the image.

| Font | Files | Source | Licence |
|---|---|---|---|
| Lato (Lukasz Dziedzic, tyPoland) | Light, Regular, Bold, Italic | Google Fonts repository, `ofl/lato/` | SIL OFL 1.1 |
| Raleway (Matt McInerney, Pablo Impallari, Rodrigo Fuenzalida, Igino Marini) | Medium, Bold | github.com/impallari/Raleway, `fonts/v3.000 Fontlab/TTF/` (static; XeTeX cannot use the variable build) | SIL OFL 1.1 |

Both fetches name a branch (`main`, `master`), because a commit could not be looked up from this build environment;
the SHA-256 pins make the content, not the branch, the authority. Raleway's italic is a slanted Medium
(`FakeSlant`) because upstream publishes no matching static italic at that path.
