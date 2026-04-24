import os
import sys
from pathlib import Path


def config_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "clevernotes" / "config.env"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "clevernotes" / "config.env"


def load() -> dict[str, str]:
    path = config_path()
    if not path.exists():
        raise SystemExit(
            f"config not found at {path}\n"
            f"run the installer first: ./install.sh (mac/linux) or install.ps1 (windows)"
        )
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")

    required = ["GEMINI_API_KEY_STAGES12", "GEMINI_API_KEY_STAGE3"]
    missing = [k for k in required if not values.get(k)]
    if missing:
        raise SystemExit(
            f"missing keys in {path}: {', '.join(missing)}\n"
            f"re-run the installer to set them"
        )
    # GEMINI_API_KEY_STAGE3_BACKUP is optional — when present the Stage 3
    # chat transparently fails over to it on daily-quota exhaustion or a
    # stubborn rate limit on the primary.
    values.setdefault("GEMINI_API_KEY_STAGE3_BACKUP", "")

    # Stages 1 & 2 use Gemma 4 26B (classification + grouping — lightweight).
    # Stage 3 uses Gemma 4 31B (notes generation — heavier reasoning).
    # Both are free-tier on Google AI Studio. Override in config.env if Google
    # changes the IDs or you want to swap to a Gemini model.
    values.setdefault("GEMINI_MODEL_STAGE1", "gemma-4-26b-a4b-it")
    values.setdefault("GEMINI_MODEL_STAGE2", "gemma-4-26b-a4b-it")
    values.setdefault("GEMINI_MODEL_STAGE3", "gemma-4-31b-it")
    values.setdefault("MAX_SLIDES_PER_GROUP", "5")
    values.setdefault("MAX_GROUPS_PER_PPTX", "120")
    return values
