#!/usr/bin/env bash
# clevernotes installer (macOS + Linux)
set -e

GREEN=$'\033[0;32m'
YELLOW=$'\033[0;33m'
RED=$'\033[0;31m'
CYAN=$'\033[0;36m'
DIM=$'\033[2m'
BOLD=$'\033[1m'
RESET=$'\033[0m'

step() { printf "${BOLD}${CYAN}[%s/%s]${RESET} %s" "$1" "$2" "$3"; }
ok()   { printf " ${GREEN}✓${RESET} %s\n" "$1"; }
warn() { printf "${YELLOW}! %s${RESET}\n" "$1"; }
fail() { printf "${RED}✗ %s${RESET}\n" "$1" >&2; exit 1; }

TOTAL=8
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

OS="$(uname -s)"
case "$OS" in
  Darwin) PLATFORM="mac" ;;
  Linux)  PLATFORM="linux" ;;
  *) fail "Unsupported OS: $OS (macOS and Linux only — use install.ps1 on Windows)" ;;
esac

printf "${BOLD}CleverNotes installer (%s)${RESET}\n\n" "$PLATFORM"

# ---- [1/8] Python ----
step 1 $TOTAL "Checking Python 3.10+ "
PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    ver=$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo "")
    major=$("$candidate" -c 'import sys; print(sys.version_info[0])' 2>/dev/null || echo "0")
    minor=$("$candidate" -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo "0")
    if [ "$major" = "3" ] && [ "$minor" -ge 10 ]; then
      PYTHON="$candidate"
      ok "found $candidate ($ver)"
      break
    fi
  fi
done
if [ -z "$PYTHON" ]; then
  printf "\n"
  if [ "$PLATFORM" = "mac" ]; then
    warn "Python 3.10+ not found. Installing via Homebrew..."
    command -v brew >/dev/null || fail "Homebrew not found. Install it from https://brew.sh first."
    brew install python@3.11 || fail "brew install python@3.11 failed"
    PYTHON="python3.11"
  elif command -v apt-get >/dev/null 2>&1; then
    warn "Python 3.10+ not found. Installing via apt..."
    # python3-venv pulled in now so step 6 doesn't need a second sudo.
    sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip \
      || fail "apt install of python3 failed — install it manually and re-run."
    PYTHON="python3"
  elif command -v dnf >/dev/null 2>&1; then
    warn "Python 3.10+ not found. Installing via dnf..."
    sudo dnf install -y python3 python3-pip \
      || fail "dnf install of python3 failed — install it manually and re-run."
    PYTHON="python3"
  elif command -v pacman >/dev/null 2>&1; then
    warn "Python 3.10+ not found. Installing via pacman..."
    sudo pacman -S --noconfirm --needed python python-pip \
      || fail "pacman install of python failed — install it manually and re-run."
    PYTHON="python3"
  else
    fail "Python 3.10+ not found and no supported package manager (apt/dnf/pacman). Install it manually (e.g. 'sudo apt install python3.11 python3.11-venv') and re-run."
  fi
  # Re-verify: the package manager gave us *something* called python3, but
  # on very old distros it might still be <3.10. Fail loudly instead of
  # silently proceeding into a broken install.
  ver=$("$PYTHON" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo "")
  major=$("$PYTHON" -c 'import sys; print(sys.version_info[0])' 2>/dev/null || echo "0")
  minor=$("$PYTHON" -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo "0")
  if [ "$major" != "3" ] || [ "$minor" -lt 10 ]; then
    fail "installed $PYTHON is $ver — need 3.10+. Upgrade your distro or install a newer python (e.g. pyenv) and re-run."
  fi
  ok "installed $PYTHON ($ver)"
fi

# Debian/Ubuntu split `venv` into a separate package — check explicitly so
# step 6 doesn't blow up on a system where python3 is present but the venv
# module isn't.
if ! "$PYTHON" -c 'import venv' >/dev/null 2>&1; then
  printf "\n"
  warn "$PYTHON is missing the \`venv\` module."
  if [ "$PLATFORM" = "linux" ] && command -v apt-get >/dev/null 2>&1; then
    pkg="${PYTHON}-venv"
    warn "Installing $pkg via apt..."
    sudo apt-get update && sudo apt-get install -y "$pkg" || fail "$pkg install failed — install it manually and re-run."
  else
    fail "Install the venv module for $PYTHON (e.g. 'sudo apt install ${PYTHON}-venv') and re-run."
  fi
  ok "venv module available"
fi

# ---- [2/8] LibreOffice ----
step 2 $TOTAL "Installing LibreOffice ................"
if command -v libreoffice >/dev/null 2>&1 || command -v soffice >/dev/null 2>&1 || [ -d "/Applications/LibreOffice.app" ]; then
  ok "already installed"
else
  printf "\n"
  if [ "$PLATFORM" = "mac" ]; then
    command -v brew >/dev/null || fail "Homebrew not found. Install it from https://brew.sh first."
    brew install --cask libreoffice || fail "brew install libreoffice failed"
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y libreoffice
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y libreoffice
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --noconfirm libreoffice-fresh
  else
    fail "No supported package manager found. Install LibreOffice manually and re-run."
  fi
  ok "installed"
fi

# ---- [3/8] Poppler ----
step 3 $TOTAL "Installing Poppler (pdftoppm) ........"
if command -v pdftoppm >/dev/null 2>&1; then
  ok "already installed"
else
  printf "\n"
  if [ "$PLATFORM" = "mac" ]; then
    brew install poppler || fail "brew install poppler failed"
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get install -y poppler-utils
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y poppler-utils
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --noconfirm poppler
  else
    fail "No supported package manager. Install poppler-utils manually."
  fi
  ok "installed"
fi

# ---- [4/8] Pandoc + XeLaTeX (for PDF export stage) ----
step 4 $TOTAL "Installing Pandoc + XeLaTeX (PDF export)"
if command -v pandoc >/dev/null 2>&1 && command -v xelatex >/dev/null 2>&1; then
  ok "already installed"
else
  printf "\n"
  if [ "$PLATFORM" = "mac" ]; then
    command -v brew >/dev/null || fail "Homebrew not found. Install it from https://brew.sh first."
    # pandoc 3.x (YAML-strict — the pdf stage disables yaml_metadata_block to cope)
    if ! command -v pandoc >/dev/null 2>&1; then
      brew install pandoc || fail "brew install pandoc failed"
    fi
    # BasicTeX is ~100 MB and ships xelatex; MacTeX (~4 GB) also works and
    # advanced users may already have it.
    if ! command -v xelatex >/dev/null 2>&1; then
      brew install --cask basictex || fail "brew install --cask basictex failed"
      warn "BasicTeX installed. You must run:  eval \"\$(/usr/libexec/path_helper)\""
      warn "or open a NEW terminal before xelatex is on your PATH."
    fi
  elif command -v apt-get >/dev/null 2>&1; then
    # texlive-xetex (~400 MB with recommended) — smallest apt bundle that
    # includes xelatex + the fonts pandoc's default template needs.
    sudo apt-get install -y pandoc texlive-xetex texlive-fonts-recommended
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y pandoc texlive-xetex texlive-collection-fontsrecommended
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --noconfirm pandoc texlive-xetex texlive-fontsrecommended
  else
    warn "No supported package manager found. Install pandoc + a XeLaTeX-capable"
    warn "TeX distribution manually if you want the clevernotes PDF stage to run."
  fi
  ok "installed"
fi

# ---- [5/8] Install app code ----
step 5 $TOTAL "Installing clevernotes code ..........."
CN_HOME="${CLEVERNOTES_HOME:-$HOME/.local/share/clevernotes}"
mkdir -p "$CN_HOME"
# Copy this repo (src/ + launcher) into CN_HOME. PDF export is now a stage
# of the main pipeline and lives inside src/clevernotes/pipeline/pdf.py.
rsync -a --delete --exclude NOTES --exclude '.git' "$SCRIPT_DIR/src/" "$CN_HOME/src/"
cp "$SCRIPT_DIR/clevernotes" "$CN_HOME/clevernotes"
cp "$SCRIPT_DIR/requirements.txt" "$CN_HOME/requirements.txt"
chmod +x "$CN_HOME/clevernotes"
ok "copied to $CN_HOME"

# ---- [6/8] venv + pip install ----
step 6 $TOTAL "Creating venv + installing Python deps"
printf "\n"
VENV="$CN_HOME/venv"
if [ ! -d "$VENV" ]; then
  "$PYTHON" -m venv "$VENV" || fail "venv creation failed"
fi
# shellcheck disable=SC1091
. "$VENV/bin/activate"
pip install --upgrade pip >/dev/null
pip install -r "$CN_HOME/requirements.txt" || fail "pip install failed"
deactivate
ok "venv + deps ready"

# ---- [7/8] API keys ----
step 7 $TOTAL "Configuring API keys ..................."
printf "\n\n"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/clevernotes"
mkdir -p "$CONFIG_DIR"
CONFIG="$CONFIG_DIR/config.env"

echo "${DIM}CleverNotes uses Google AI Studio API keys. Each free-tier key is capped at"
echo "15 RPM / 1500 RPD, so using keys from DIFFERENT Google accounts draws from"
echo "independent quota pools. You'll enter up to THREE keys:"
echo "  1. GEMINI_API_KEY_STAGES12 — classification + grouping (stages 1+2)"
echo "  2. GEMINI_API_KEY_STAGE3   — notes generation (stage 3), primary"
echo "  3. GEMINI_API_KEY_STAGE3_BACKUP — stage 3 fallback (OPTIONAL)"
echo "     When the primary stage-3 key hits its daily quota, clevernotes"
echo "     transparently fails over to this backup and replays the chat"
echo "     history so cross-group context within a file is preserved."
echo "Get keys at: https://aistudio.google.com — click 'Get API key'."
echo "Keys will be shown as you type (not hidden).${RESET}"
echo

echo "If you already have a config, re-running will overwrite it."

while true; do
  read -r -p "Enter ${BOLD}GEMINI_API_KEY_STAGES12${RESET} (stages 1 + 2):             " KEY1
  read -r -p "Enter ${BOLD}GEMINI_API_KEY_STAGE3${RESET}  (stage 3 primary):           " KEY2
  read -r -p "Enter ${BOLD}GEMINI_API_KEY_STAGE3_BACKUP${RESET} (stage 3 backup, blank to skip): " KEY3

  if [ -z "$KEY1" ]; then
    warn "KEY 1 cannot be empty. Let's try again."
    echo
    continue
  fi

  if [ -z "$KEY2" ]; then
    warn "KEY 2 is empty — stage 3 will fall back to KEY 1."
    printf "${DIM}Both stages would then share the same free-tier pool (15 RPM / 1500 RPD),\n"
    printf "so effective throughput is halved. That's fine if it's intentional.${RESET}\n"
    read -r -p "Proceed with KEY 1 for both pools? [Y/n]: " ANS
    case "$ANS" in
      n|N|no|No|NO) echo; echo "Let's re-enter the keys."; echo; continue ;;
      *) KEY2="$KEY1" ;;
    esac
  elif [ "$KEY1" = "$KEY2" ]; then
    warn "KEY 1 and KEY 2 are identical."
    printf "${DIM}They'll share the same free-tier pool (15 RPM / 1500 RPD), so effective\n"
    printf "throughput is halved. This is fine if intentional — for best results you could\n"
    printf "use two keys from two different Google accounts (https://aistudio.google.com).${RESET}\n"
    read -r -p "Proceed with the same key for both, or re-enter them? [Y=proceed / n=re-enter]: " ANS
    case "$ANS" in
      n|N|no|No|NO) echo; echo "Let's re-enter the keys."; echo; continue ;;
      *) : ;;
    esac
  fi

  # Validate the optional backup. Empty is fine (we just skip failover).
  # Identical-to-primary is useless (same quota pool) — warn and drop it.
  if [ -n "$KEY3" ] && [ "$KEY3" = "$KEY2" ]; then
    warn "KEY 3 (stage 3 backup) is identical to KEY 2 (stage 3 primary)."
    printf "${DIM}Since both would draw from the same quota pool, the fallback would be dead\n"
    printf "on arrival. Dropping the backup — set it to a key from a DIFFERENT Google account\n"
    printf "for the failover to actually help.${RESET}\n"
    KEY3=""
  fi
  break
done

cat > "$CONFIG" <<EOF
# clevernotes config — edit manually or re-run install.sh
GEMINI_API_KEY_STAGES12=$KEY1
GEMINI_API_KEY_STAGE3=$KEY2
# Optional: when the stage-3 primary hits its daily quota or a stubborn rate
# limit, clevernotes fails over to this key (chat history is replayed so
# cross-group context within a file is kept). Leave blank to disable.
GEMINI_API_KEY_STAGE3_BACKUP=$KEY3

# Model overrides (optional — defaults are gemma-4-26b-a4b-it for stages 1+2
# and gemma-4-31b-it for stage 3).
# GEMINI_MODEL_STAGE1=gemma-4-26b-a4b-it
# GEMINI_MODEL_STAGE2=gemma-4-26b-a4b-it
# GEMINI_MODEL_STAGE3=gemma-4-31b-it

# Pipeline tuning
# MAX_SLIDES_PER_GROUP=5
# MAX_GROUPS_PER_PPTX=120
EOF
chmod 600 "$CONFIG"
ok "saved to $CONFIG (mode 600)"

# ---- [8/8] launcher on PATH ----
step 8 $TOTAL "Installing \`clevernotes\` on PATH ......"
printf "\n"
LAUNCHER_TARGET=""

if [ -w "/usr/local/bin" ] 2>/dev/null; then
  ln -sf "$CN_HOME/clevernotes" "/usr/local/bin/clevernotes"
  LAUNCHER_TARGET="/usr/local/bin/clevernotes"
elif [ "${CLEVERNOTES_NO_SUDO:-0}" = "1" ]; then
  : # skip sudo path
elif command -v sudo >/dev/null 2>&1; then
  if sudo -n true 2>/dev/null; then
    # passwordless sudo available — proceed silently
    sudo ln -sf "$CN_HOME/clevernotes" "/usr/local/bin/clevernotes" \
      && LAUNCHER_TARGET="/usr/local/bin/clevernotes" || true
  else
    echo
    if [ "$PLATFORM" = "mac" ]; then
      pwd_hint="your ${BOLD}macOS login password${RESET}${DIM} — the same one you use at login or in the App Store"
    else
      pwd_hint="your ${BOLD}sudo password${RESET}${DIM} — the same one you use for any \`sudo\` command"
    fi
    echo "${DIM}The installer wants to put \`clevernotes\` into /usr/local/bin so you can run it"
    echo "from anywhere. Writing to /usr/local/bin requires sudo, so the next prompt"
    printf "('Password:') is asking for %s.\n" "$pwd_hint"
    echo "Nothing will be shown as you type; that's normal for sudo."
    echo
    echo "If you'd rather skip sudo, press Ctrl+C and re-run with:"
    echo "    ${BOLD}CLEVERNOTES_NO_SUDO=1 ./install.sh${RESET}${DIM}"
    echo "which installs the launcher into \$HOME/.local/bin instead.${RESET}"
    echo
    if sudo ln -sf "$CN_HOME/clevernotes" "/usr/local/bin/clevernotes"; then
      LAUNCHER_TARGET="/usr/local/bin/clevernotes"
    fi
  fi
fi

if [ -z "$LAUNCHER_TARGET" ]; then
  mkdir -p "$HOME/.local/bin"
  ln -sf "$CN_HOME/clevernotes" "$HOME/.local/bin/clevernotes"
  LAUNCHER_TARGET="$HOME/.local/bin/clevernotes"
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) warn "$HOME/.local/bin is not on your PATH. Add this to your ~/.zshrc or ~/.bashrc:"
       printf "    ${BOLD}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}\n" ;;
  esac
fi
ok "$LAUNCHER_TARGET"

printf "\n${GREEN}${BOLD}Installation complete.${RESET}\n\n"
echo "Next steps:"
echo "  1. Create a folder anywhere, e.g. ~/Desktop/blockchain-unit-3/"
echo "  2. Put your lecture materials inside, named file_1.pptx, file_2.pptx, ... (PDFs also supported, e.g. file_2.pdf)"
echo "  3. cd into the folder and run: ${BOLD}clevernotes${RESET}"
echo "     (PDFs are rendered automatically at the end of the pipeline — no extra step needed.)"
echo
