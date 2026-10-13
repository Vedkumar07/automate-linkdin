---
name: automate-linkdin
description: Draft LinkedIn posts, comments, replies, and weekly plans using the user's voice, or set up and operate the local Cadence LinkedIn assistant. Use for LinkedIn writing and Cadence setup, not general social media automation.
---

# Automate LinkedIn with Cadence

Support two modes: writing directly in the agent chat, and operating the local Cadence application. Chat drafting needs no API keys or model downloads. Cadence uses Ollama for local generation, with optional Telegram approvals and LinkedIn API posting.

## Writing in chat

Read the user's voice.md, profile.md, and stories.md from their Cadence checkout if they have supplied its location. Otherwise use the user's writing samples and facts from the conversation. Ask for missing facts only when needed; mark unknown metrics as {{your number}}. Do not invent experience, clients, or results.

For a post, offer a few distinct opening lines and one complete draft in the user's voice. Prefer a concrete opening, short paragraphs, and a specific ending. For comments or replies, respond to the substance of the supplied post or thread. For a weekly plan, use the user's actual topics and time constraints. Return copy-ready text for review.

## Set up Cadence

The source is https://github.com/Vedkumar07/automate-linkdin. Locate an existing checkout before cloning another one. Work from its root.

- Docker: run `./setup.sh`. This creates missing local settings, starts Ollama, downloads the configured writing and vision models, and builds the dashboard at http://localhost:8787. It preserves existing settings. Docker Compose, Python 3, sufficient disk space, and access to the Docker daemon are required.
- Local Python: run `./setup.sh --local`. Ollama must already be installed. Pull the models listed in config.json, then run `python3 -m cadence init` interactively and `python3 -m cadence run`. Optional carousel PDFs need `python3 -m pip install -r requirements.txt` in a virtual environment.
- Add writing samples on the Voice page and real facts on Background. See the checkout's README.md for LinkedIn OAuth and Telegram setup.

Credentials belong in the ignored .env; tokens and history belong in ignored data/. Do not put credentials in chat, the skill, or Git. If the execution environment blocks Docker or sockets, report the specific blocker and provide the command for the user's terminal; do not claim the service was started.

## Use local tools

From the checkout root, `python3 -m cadence tools TOOL FILE` reads a file; use `-` to read stdin. Tools are `check`, `hooks`, `humanize`, `beats`, `audit`, and `swipe`. For Docker, use `docker compose exec -T cadence python -m cadence tools TOOL -` and pipe the text through stdin. These tools run locally; their human score is a heuristic, not a detector guarantee.

Use `docker compose exec -T cadence python -m cadence doctor` to diagnose Docker setup, or `python3 -m cadence doctor` locally. Missing optional credentials can produce FIX rows even while the dashboard is available.

Draft generation is `python3 -m cadence draft` locally or `docker compose exec -T cadence python -m cadence draft` in Docker. This stores a draft and may send it to the configured Telegram chat; explain that effect when relevant. Cadence keeps posting approval on by default. A request to draft or install does not authorize publishing posts, sending messages, or enabling autoPost. Use the dashboard or Telegram approval flow for publication when requested. Comments, replies, and DMs are drafts for manual posting.
