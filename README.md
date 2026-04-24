# CleverNotes

Terminal tool that turns your lecture slides (`.pptx` or `.pdf`) into a single Markdown + PDF study doc, grouped by topic with slide images inlined. Runs locally, uses the Google AI Studio free tier to generate the notes.

Works on **macOS**, **Linux**, and **Windows** (via Docker Desktop).

---

## How it works

You drop your lecture decks in a folder, run `clevernotes`, and get back a `NOTES/final_notes/combined_notes.md` (plus per-deck `file_N.md` and PDF versions of both) with AI-generated study notes, grouped by topic.

Pipeline stages:

1. **Convert** each input to per-slide PNGs (LibreOffice for `.pptx`, Poppler for `.pdf`).
2. **Classify** each slide as USEFUL or USELESS. Title slides, disclaimers, thank-you pages, etc. get dropped.
3. **Group** the useful slides by topic cohesion (max 5 slides per group).
4. **Generate** Markdown notes per group, using a multi-turn chat so later groups know what earlier ones covered.
5. **Render** Markdown → PDF via Pandoc + XeLaTeX.

---

## Install

### Prerequisites

- **macOS**: [Homebrew](https://brew.sh). The installer uses `brew` for any missing system packages.
- **Linux**: a mainstream distro with `apt` (Debian/Ubuntu), `dnf` (Fedora), or `pacman` (Arch). `sudo` access.
- **Windows**: [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running. (Docker Desktop needs WSL2 and may ask for a reboot on first install.)

### macOS / Linux

```bash
git clone https://github.com/AryanGiri002/clevernotes-terminalV.git
cd clevernotes-terminalV
./install.sh
```

The installer will:
- Check/install Python 3.10+, LibreOffice, Poppler, Pandoc + XeLaTeX.
- Create a venv and install the Python deps.
- Prompt for your API keys and save them to `~/.config/clevernotes/config.env`.
- Put a `clevernotes` launcher on your `PATH`.

You'll be asked for your **login / sudo password** at some point — that's because the installer symlinks the launcher into `/usr/local/bin/`. If you'd rather skip `sudo`, rerun with:

```bash
CLEVERNOTES_NO_SUDO=1 ./install.sh
```

This installs the launcher into `~/.local/bin/clevernotes` instead. Make sure `~/.local/bin` is on your `PATH`.

Rerunning `./install.sh` is safe — it reuses the existing venv, skips already-installed system packages, and just overwrites the code + config.

### Windows (via Docker Desktop)

The Windows install is containerized — we ship a prebuilt Ubuntu image to Docker Hub (`002giriaryan/clevernotes:latest`) with all of clevernotes and its system dependencies preinstalled, so you skip the native-Windows packaging grief.

Make sure Docker Desktop is running (whale icon in the system tray, stopped animating), then in PowerShell:

```powershell
git clone https://github.com/AryanGiri002/clevernotes-terminalV.git
cd clevernotes-terminalV
.\install.ps1
```

The installer will:
- Verify Docker Desktop is reachable.
- `docker pull` the image (~2.15 GB, one-time).
- Prompt for your API keys and save them to `%APPDATA%\clevernotes\config.env`.
- Drop a `clevernotes.cmd` wrapper into `%LOCALAPPDATA%\clevernotes\bin` and add it to your User PATH.

**Open a new terminal after install** so the PATH change takes effect, then use `clevernotes` exactly like on macOS/Linux (see below).

Under the hood, the launcher bind-mounts your current folder into the container at `/work`, so any `NOTES\` directory it generates appears directly in your Windows folder.

---

## Getting API keys

Go to [aistudio.google.com](https://aistudio.google.com), click **Get API key**, and create a new key. Do this **at least twice, from two different Google accounts** — clevernotes uses one key for stages 1+2 (classification + grouping) and a separate key for stage 3 (notes generation), so each draws from its own free-tier quota pool (15 RPM / 1500 RPD).

The installer also asks for an **optional third key** as a stage-3 backup. When the stage-3 primary hits its daily quota (1500 RPD) or a stubborn per-minute rate limit, clevernotes transparently fails over to the backup key and **replays the chat history** onto the new client, so cross-group context within a file is preserved. Leave it blank to disable failover.

If you only have one Google account, you can enter the same key for STAGES12 and STAGE3 — throughput will be halved.

---

## Use

### 1. Make a folder for the unit

```bash
mkdir ~/Desktop/blockchain-unit-3
cd ~/Desktop/blockchain-unit-3
```

### 2. Put your lecture materials inside, renamed to `file_N.pptx` or `file_N.pdf`

clevernotes looks for files matching the pattern `file_<N>.pptx` or `file_<N>.pdf` in the current folder (case-insensitive). Anything with a different name is ignored. **You must rename your decks** before running the tool.

Rules:
- The number `N` must start at `1` and increment (`file_1`, `file_2`, `file_3`, ...).
- `N` controls ordering in `combined_notes.md` — `file_1` appears first, `file_2` second, etc. Number them in the order you want to study them.
- `.pptx` and `.pdf` are treated identically — you can mix them freely in the same folder (e.g. `file_1.pptx`, `file_2.pdf`, `file_3.pptx`).
- Don't use both `file_3.pptx` *and* `file_3.pdf` in the same folder — the tool will error out rather than pick one for you.

Example — renaming downloaded lecture decks from Moodle/Blackboard:

```bash
mv "Lecture 1 - Intro to Consensus.pptx"          file_1.pptx
mv "Lecture 2 - Proof of Work (slides).pdf"       file_2.pdf
mv "Lecture 3 - Byzantine Fault Tolerance.pptx"   file_3.pptx
```

After renaming, your folder should look like this:

```
~/Desktop/blockchain-unit-3/
├── file_1.pptx
├── file_2.pdf
└── file_3.pptx
```

### 3. Run it

```bash
clevernotes
```

On first run in a folder you'll answer a short questionnaire that tunes the note style (bulleted vs prose, with/without analogies, ELI5 vs rigorous, concise vs thorough, etc.). Answers are cached to `NOTES/.presets.json`.

### Flags

```bash
clevernotes --reset-presets   # re-ask the questionnaire
clevernotes --default         # skip the questionnaire, use sensible defaults
clevernotes --version
```

### Folder layout after a run

```
~/Desktop/blockchain-unit-3/
├── file_1.pptx
├── file_2.pdf
└── NOTES/
    ├── file_1/
    │   ├── 1.png, 2.png, ...                  # per-slide PNGs
    │   ├── 3_DISCARDED.png                    # useless slides renamed, not deleted
    │   ├── summary_file_1.json                # stage-1 output
    │   └── file_1_grouping_phase_summary.json # stage-2 output
    ├── file_2/...
    ├── .presets.json                          # your cached preferences
    └── final_notes/
        ├── file_1.md       file_1.pdf         # notes for file_1 only
        ├── file_2.md       file_2.pdf         # notes for file_2 only
        └── combined_notes.md  combined_notes.pdf   # everything concatenated
```

Per-file and combined outputs are appended incrementally as each group finishes, so you can read `file_1.md` while `file_2` is still generating. PDFs are rendered automatically at the end of the run (or regenerated on rerun if the `.md` is newer than the `.pdf`).

---

## Rerun / resume

Safe to rerun anytime. Each stage skips work that already finished:

- Stage 1 skips a file if `summary_file_N.json` exists.
- Stage 2 skips a file if `file_N_grouping_phase_summary.json` exists.
- Stage 3 writes an HTML-comment marker after each completed group and another after the last group in a file. On rerun, any group whose marker is present is skipped; any half-written tail is truncated and regenerated.
- Stage 4 skips rendering a PDF if it already exists and is newer than its source `.md`.

To regenerate just stage-3 + stage-4 output (without paying for stages 1 and 2 again), delete `NOTES/final_notes/*.md` and rerun.

---

## Config

Location:

- **macOS / Linux**: `~/.config/clevernotes/config.env`
- **Windows**: `%APPDATA%\clevernotes\config.env`

```
GEMINI_API_KEY_STAGES12=...
GEMINI_API_KEY_STAGE3=...
GEMINI_API_KEY_STAGE3_BACKUP=...   # optional — stage 3 failover
# GEMINI_MODEL_STAGE1=gemma-4-26b-a4b-it
# GEMINI_MODEL_STAGE2=gemma-4-26b-a4b-it
# GEMINI_MODEL_STAGE3=gemma-4-31b-it
# MAX_SLIDES_PER_GROUP=5
# MAX_GROUPS_PER_PPTX=120
```

Rerun the installer anytime to overwrite the keys, or edit the file directly.

---

## Caveats

- **Windows = Docker**: you need Docker Desktop running before you invoke `clevernotes`. If you get `Docker daemon is not reachable`, start Docker Desktop and wait for the whale icon to settle.
- **LibreOffice layout drift**: very fancy slides (exotic fonts, complex animations) may render slightly differently than in PowerPoint. For study notes this is almost always fine.
- **Headless Linux**: on minimal server distros with no desktop environment, `soffice` can render slides with missing or substituted fonts. If your PNGs look off, install a base font set:

  ```bash
  sudo apt install fonts-liberation fonts-dejavu fonts-noto             # Debian/Ubuntu
  sudo dnf install liberation-fonts dejavu-sans-fonts google-noto-sans-fonts  # Fedora
  ```

- **Free-tier rate limits**: a typical 20–40 slide deck fits comfortably. Very large batches may hit the 15 RPM per-minute limit; clevernotes retries with backoff, and with a stage-3 backup key it will fail over automatically on persistent limits.
