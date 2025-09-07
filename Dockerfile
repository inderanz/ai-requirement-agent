# ai-pdf-agent runner image: Python 3.11 + OCR + PDF tooling
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies for OCR and rendering plus GitHub CLI for
# interacting with GitHub Pull Requests and issue comments. We avoid
# unnecessary packages to keep the image small.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr ghostscript libgl1 ca-certificates curl git gnupg jq && \
    rm -rf /var/lib/apt/lists/*

# Install GitHub CLI. This is optional but useful for workflows that
# manipulate pull requests and comments directly from scripts.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  > /etc/apt/sources.list.d/github-cli.list && \
  apt-get update && apt-get install -y gh && \
  rm -rf /var/lib/apt/lists/*

# Set up working directory and copy dependencies file. We do not copy
# application code here so that the image remains generic and can be
# reused across multiple projects.
WORKDIR /workspace
COPY requirements.txt /tmp/requirements.txt

# Install Python dependencies. By installing them in the image build
# step we avoid re-running pip install on every workflow run.
RUN python -m pip install --upgrade pip && \
    pip install -r /tmp/requirements.txt

# Set the default working directory. The GitHub action runner will
# mount the repository in /workspace when the container executes.
WORKDIR /workspace