# The loop's check container (build as job-search-check:2): LaTeX (lualatex, xelatex, pdflatex), poppler, python3
# and the open-licensed letter fonts. It runs with no network and as uid 1000.
#   docker build -t job-search-check:2 check
# On a network that re-signs TLS, put the proxy's CA certificates (*.crt) in check/extra-ca/ first; they are trusted
# before any https fetch. On a normal network that directory stays empty.
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
COPY extra-ca/ /usr/local/share/ca-certificates/extra/
RUN update-ca-certificates

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-luatex texlive-xetex \
      texlive-fonts-recommended texlive-fonts-extra cm-super lmodern fontconfig \
      poppler-utils python3 python3-httpx \
 && rm -rf /var/lib/apt/lists/*

# Lato (SIL OFL 1.1, Lukasz Dziedzic) comes from the Google Fonts repository; Raleway (SIL OFL 1.1, Matt McInerney,
# Pablo Impallari and others) from its upstream repository impallari/Raleway (static v3.000 files, XeTeX cannot use the
# variable build). Every file is checked against a pinned SHA-256, so an upstream change fails the build; both are used
# unmodified. Their licences are saved beside them.
ARG LATO_BASE=https://raw.githubusercontent.com/google/fonts/main/ofl/lato
ARG RALEWAY_BASE=https://raw.githubusercontent.com/impallari/Raleway/master
WORKDIR /usr/local/share/fonts/jobloop
RUN set -e; \
    fetch() { curl -fsSL --retry 3 -o "$2" "$1"; echo "$3  $2" | sha256sum -c -; }; \
    fetch ${LATO_BASE}/Lato-Light.ttf Lato-Light.ttf cf2a774503baf418d584f49967bd160e1e03f087c13b25602f28024ec7788f08; \
    fetch ${LATO_BASE}/Lato-Regular.ttf Lato-Regular.ttf d636e4683231f931eda222d588e944d082bfd3bdba02f928bee461c0f185b251; \
    fetch ${LATO_BASE}/Lato-Bold.ttf Lato-Bold.ttf 8a0aace75d33794eece4b28187bfc1df0bbd2888b5d8a56e01788c8d65d16be1; \
    fetch ${LATO_BASE}/Lato-Italic.ttf Lato-Italic.ttf e399c44efe1387100531d26c7e4800c5d12251b890d6654a3098c7c679cb1786; \
    fetch "${RALEWAY_BASE}/fonts/v3.000%20Fontlab/TTF/Raleway-Medium.ttf" Raleway-Medium.ttf 84e15d32decbb3e051c169b4023d7debafa044587b8f12eeb3324f4ed2683f48; \
    fetch "${RALEWAY_BASE}/fonts/v3.000%20Fontlab/TTF/Raleway-Bold.ttf" Raleway-Bold.ttf 4bbee5ac4b10b4fbe5fdb0bff6a75898d8800c62ad16311484e8680ef46b5701; \
    fetch ${LATO_BASE}/OFL.txt OFL-Lato.txt 74ba064d03f1f1c4a952da936c3eb71866c34404916734de3cae73b34357e59e; \
    fetch ${RALEWAY_BASE}/OFL.txt OFL-Raleway.txt e588abf45ca2b19c340c4b27349c9b6b6e5e198316fd965128ed9f4422942cbe; \
    fc-cache -f

RUN useradd --create-home --uid 1000 checker
USER checker
WORKDIR /home/checker
# lualatex's font index is built now, because the container runs with a read-only root.
RUN luaotfload-tool --update && fc-cache -f
