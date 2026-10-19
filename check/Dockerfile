# The loop's own check container: LaTeX, poppler and python for checking portal adapters. Runs with no network.
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends texlive-latex-base texlive-latex-recommended texlive-fonts-recommended cm-super lmodern poppler-utils python3 python3-httpx \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 checker
USER checker
