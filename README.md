# Cadence

A LinkedIn writing assistant that runs on your own machine. A local model writes in your voice, a quality gate catches machine-sounding drafts, and nothing posts until you approve it on Telegram or the dashboard. Posting uses LinkedIn's official API. Model inference runs locally; news feeds, Telegram, and LinkedIn use network requests when configured.

## What it does

**Writing (on schedule, Mon/Wed/Fri at 9:00 by default)**
- Picks what to write about: anything in your inbox first, then a mix of your background, a news take, or a reflection.
- Writes the post, then checks it: banned phrases, emoji, lists, "not just X, it's Y", flat rhythm, generic closers, invented numbers, and a human-sounding score. A weak opening line gets three alternatives, and the best one is kept only if it clearly beats the original.
- Sends the draft to Telegram and the dashboard: Post it, Skip, Different take, or reply with your own edit.

**Photos.** Send a photo to the bot with a caption. A local vision model describes it, the post is written around it, and the photo goes up with proper alt text. The model never names people.

**Carousels.** Give a topic, get slide copy and a PDF that is posted as a LinkedIn document. Needs `reportlab`.

**Studio** (dashboard tab, or Telegram where noted). Every tool is a paste-and-result form:

| Tool | What it does |
|---|---|
| Check a draft (`/check` on Telegram) | Cleans invisible characters and typography, scores how human it reads (five checks), grades the opening line, and shows the preview before "see more" with a lint of the rules |
| Rank opening lines | Ranks several candidate first lines, with the five hook checks and the formula each resembles |
| Comment on someone's post | Picks the comment type that fits the post and writes one under 45 words. You paste it yourself |
| Reply to comments on my post | Sorts the thread so leads and questions come first, skips noise, and drafts replies |
| Direct message | Connection note (200-character limit, checked), first message, or a follow-up. Stops after two follow-ups |
| Inbox triage | Paste messages, one block each. Sorted into leads, recruiters, questions, peers and spam, with the word that decided each. Replies drafted for the ones worth answering |
| Repurpose a source | Finds standalone angles in your own article or transcript and saves each to the inbox |
| Carousel | Topic in, slides and PDF out, waiting for your approval |
| My post analytics | CSV of your posts, ranked by multiple over your own median and engagement rate, with top-third vs bottom-third features and a stop-doing list |
| Swipe file | CSV of posts you looked at, ranked per account, so a small account's breakout is compared with its own median |
| Profile audit | Scores your profile out of 100 and lists the fixes that buy the most points. Optional rewrite of the headline and first two lines of About (flags any number it invents) |
| Beat sheet | Times a short video script and flags slow hooks, long beats and missing loops |

**This week.** The dashboard and `/plan` show the planned slots for the next week, each one still waiting for your approval. The Plan form on the Studio also takes up to ten names or profile links you want to engage with, and suggests a comment type for each. Cadence never looks anyone up.

Command line: `python3 -m cadence tools check draft.txt` (also `hooks`, `humanize`, `beats`, `audit`, `swipe`).

## What is not here, and why

- **Stories (`/ig-story`)**: Instagram only. LinkedIn has no equivalent.
- **Reel formats and the 1080x1350 files**: Instagram only. The beat sheet covers short videos.
- **Scraping and automated engagement**: the viral tool reads only numbers you copy yourself, and comments, replies and DMs are drafted for you to post. Automating those breaks LinkedIn's terms and gets accounts restricted.
- **X (Twitter)**: its API is pay-per-use.

## Quick start with Docker

Requires Git, Python 3, Docker, and Docker Compose v2. Docker must be running and accessible to your user. The default setup uses CPU inference; larger models can be slow and download several GB. For GPU options, see [Ollama's Docker documentation](https://docs.ollama.com/docker).

```bash
git clone https://github.com/Vedkumar07/automate-linkdin.git
cd automate-linkdin
./setup.sh
```

Open **http://localhost:8787**. Setup creates your local settings from `templates/`, preserves existing files, downloads the writing and vision models, and starts the application. No credentials are required to open the dashboard or use its text checking tools. Generation requires the downloaded models; publication requires LinkedIn setup.

Add your writing samples on **Voice** and your real experiences on **Background**. Edit `.env` to add the optional credentials described below, then run `docker compose restart cadence`.

```bash
docker compose logs -f cadence              # logs
docker compose exec cadence python -m cadence doctor
docker compose down                        # stop; settings and models persist
```

The dashboard is published only on your computer's loopback address. Personal settings, credentials, drafts, and tokens stay in ignored local files. Models persist in a Docker volume. `docker compose down -v` deletes that model volume. Do not run the local and Docker dashboards on port 8787 simultaneously. Setup is safe to rerun; edit `config.json` first if you want different models. Re-run setup to pull them. Docker networking overrides the web port to 8787 and the Ollama URL to its companion container.

## Install the agent skill

For Claude Code:

```bash
mkdir -p ~/.claude/skills
cp -r skills/automate-linkdin ~/.claude/skills/
```

For Codex:

```bash
mkdir -p ~/.codex/skills
cp -r skills/automate-linkdin ~/.codex/skills/
```

Restart your agent session after installation. Ask it to use `automate-linkdin` to draft a post, make a weekly plan, or set up Cadence. Claude Code can invoke `/automate-linkdin`; Codex can invoke `$automate-linkdin`. The skill can write directly in chat without Docker or API keys. Provide your own writing samples and facts. To operate Cadence, also provide the checkout location. Installation alone does not start the application or publish posts.

Alternatively, give your agent this repository URL and ask it to install `skills/automate-linkdin` for your agent and confirm that it is available. The skill instructions are in [SKILL.md](skills/automate-linkdin/SKILL.md).

## Local Python setup

Install Ollama separately, then:

```bash
./setup.sh --local
ollama pull qwen2.5:7b
ollama pull gemma3:4b
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt    # optional carousel PDF support
.venv/bin/python -m cadence init
.venv/bin/python -m cadence doctor
.venv/bin/python -m cadence run
```

The application uses the Python standard library except for optional carousel PDFs. Python 3.10+ is required. `doctor` reports missing credentials and samples; these can be configured later. For a login-time user service, run `./install.sh` after local setup. Docker already uses a restart policy.

## Connect LinkedIn and Telegram

**LinkedIn:** create your own developer app. In its Auth tab, add this redirect URL exactly: `http://localhost:8787/oauth/callback`. Enable *Share on LinkedIn* and *Sign In with LinkedIn using OpenID Connect* as available to your app. Set `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET` in `.env`, restart Cadence, then press **Connect LinkedIn** on the dashboard. Product availability and API permissions depend on your app. Cadence warns on Telegram before the stored token expires.

**Telegram (optional):** create a bot with @BotFather, set `TELEGRAM_BOT_TOKEN` in `.env`, restart Cadence, and message the bot once. It replies with your chat id. Set `TELEGRAM_OWNER_CHAT_ID` to that id and restart. Only that chat can operate the bot after it is configured.

Never commit `.env`, `data/`, `config.json`, `voice.md`, `profile.md`, or `stories.md`. The repository includes generic templates instead.

## Make it sound like you

Use the **Voice** page to paste three to six things you really wrote. Use **Background** for real, specific, slightly embarrassing things from your work. The model copies examples far better than it follows rules.

## Settings (`config.json`)

- `schedule`, `timezone`, `maxPostsPerDay`, `catchUpHours`: when it posts
- `mix`: how often each kind of post appears
- `feeds`, `interests`: news sources and reflection topics
- `banned`: phrases never allowed (the built-in list is in `cadence/lexicon.json`)
- `previewChars`: how much of a post shows before "see more" (about 180)
- `hookMin`, `humanScoreMin`, `hookRewrite`: the quality thresholds
- `visionModel`, `photoDraftsImmediately`: photo handling
- `autoPost`: off by default. News takes and anything flagged always need your approval

## Honest limits

- The laptop must be on at post time.
- The "human score" is a set of local heuristics, not a detector. It catches obvious machine-sounding drafts and cannot promise any verdict. Read every draft.
- A 7B writer and a 4B vision model are good, not perfect. Read what the photo description says it sees.
- The carousel document upload and the photo and document paths follow LinkedIn's documented APIs but were tested here only against a stub server. Your first real post of each kind is the true test. If LinkedIn says a version is retired, change `linkedinVersion` in `config.json`.
- Comments, replies and DMs are never sent by Cadence.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Credits and licence notes are in `NOTICE.md`.
