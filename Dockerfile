# clevernotes — Ubuntu base with everything the pipeline needs.
# Used by Windows users via Docker Desktop (install.ps1 pulls this image
# from Docker Hub and wraps `docker run` behind a `clevernotes` launcher).
#
# Publish:
#   docker build -t 002giriaryan/clevernotes:latest .
#   docker push  002giriaryan/clevernotes:latest
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# System packages:
#   libreoffice + poppler-utils   → Stage 0 (pptx/pdf -> PNG)
#   pandoc + texlive-xetex + ...  → Stage 4 (md -> pdf)
#   fonts-*                       → readable headless LibreOffice + xelatex
#   python3 + python3-venv        → runtime
# --no-install-recommends keeps the image lean-ish; we add back only the
# fonts we actually need.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        python3-pip \
        libreoffice \
        poppler-utils \
        pandoc \
        texlive-xetex \
        texlive-fonts-recommended \
        fonts-liberation \
        fonts-dejavu \
        fonts-noto \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Put pip deps inside an isolated venv so they don't collide with any
# system-managed Python packages (PEP 668 on Ubuntu 24.04 rejects global
# `pip install` unless you pass --break-system-packages).
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Requirements first (cache-friendly: source changes don't re-trigger pip).
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r /app/requirements.txt

# Application code.
COPY src /app/src
ENV PYTHONPATH=/app/src

# The user's lecture folder gets bind-mounted here at `docker run` time.
# Inside the pipeline `Path.cwd()` picks up file_1.pptx / file_1.pdf from /work
# and writes NOTES/ alongside them → on Windows that shows up as NOTES\ in
# the host folder the user ran the launcher from.
WORKDIR /work

ENTRYPOINT ["python", "-m", "clevernotes"]
