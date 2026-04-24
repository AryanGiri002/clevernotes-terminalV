from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from google import genai
from google.genai import types


# Backoff schedule for transient errors (seconds). After the list is exhausted
# we plateau at the last value (1200s = 20 min). The cap on attempts is
# intentionally astronomical — we'd rather sit in a long wait loop overnight
# than give up on an 80%-done pipeline. The only way to truly stop is Ctrl+C
# or a PERMANENT error (bad API key, unknown model, 400-family).
RETRY_BACKOFF: list[int] = [
    2, 8, 30, 60, 120, 300, 600, 900, 1200,
]
MAX_BACKOFF_PLATEAU = 1200  # 20 min
MAX_ATTEMPTS = 500  # ~100+ hours of outage tolerated; effectively infinite


# Error classification — substrings to look for in str(exc). Order matters:
# permanent checks run first so a "404 UNAVAILABLE" (unlikely but possible)
# wouldn't get misclassified.
_PERMANENT_SIGNALS = (
    "invalid_argument",
    "unauthenticated",
    "permission_denied",
    "api key not valid",
    "api_key_invalid",
    "invalid api key",
    " 400 ",
    " 401 ",
    " 403 ",
    " 404 ",
    "not found for api version",
    "not found for model",
)

_TRANSIENT_SIGNALS = (
    "unavailable",
    "resource_exhausted",
    "deadline_exceeded",
    "internal",
    "rate",
    "quota",
    "429",
    "500",
    "502",
    "503",
    "504",
    "connection",
    "timed out",
    "timeout",
    "temporarily",
    "try again",
    "server disconnected",
    "remote end closed",
    "ssl",
    "socket",
    "eof occurred",
)

# Distinguish per-day quota (1500 RPD — doesn't reset until ~midnight Pacific)
# from per-minute rate limit (15 RPM — self-resolves in <60s). On a daily hit
# we should switch keys *immediately*; on a per-minute we can afford to back
# off a bit before switching. Google's error text for daily quota typically
# mentions "per day" / `generate_requests_per_day_per_project...`.
_DAILY_QUOTA_SIGNALS = (
    "per day",
    "per-day",
    "daily limit",
    "daily quota",
    "requests per day",
    "generate_requests_per_day",
    "generaterequestsperday",
)

# Any of these in an error message means "rate/quota related" — used to decide
# if we should give up on the current key after a few fruitless attempts.
_RATE_LIKE_SIGNALS = (
    "429",
    "resource_exhausted",
    "resourceexhausted",
    "quota",
    "rate",
)

# When the active stage-3 key keeps returning per-minute rate errors, give up
# on it after this many attempts on the CURRENT client and switch to the
# backup. Small enough that we don't sit through a 300s+ backoff when a fresh
# key is available; large enough to weather a genuine 15-RPM blip without
# thrashing between keys.
STAGE3_SWITCH_AFTER_ATTEMPTS = 4


class PermanentLLMError(RuntimeError):
    """Non-retryable (bad key, bad model, malformed request). Surface to user."""


class Stage3Pool:
    """Shared-across-files pool of stage-3 API clients.

    Holds 1 or 2 `genai.Client`s and remembers which have been marked
    daily-quota-exhausted so subsequent files don't waste attempts re-trying
    a key we already know is dead for today. Per-minute rate limits do NOT
    mark a key exhausted — only the daily-quota signal does, because
    per-minute limits self-heal in <60s and marking them would permanently
    disable a working key.
    """

    def __init__(self, clients: list[genai.Client]):
        assert clients, "Stage3Pool needs at least one client"
        self._clients = list(clients)
        self._exhausted: set[int] = set()

    @property
    def clients(self) -> list[genai.Client]:
        return self._clients

    def size(self) -> int:
        return len(self._clients)

    def first_available(self) -> int | None:
        for i in range(len(self._clients)):
            if i not in self._exhausted:
                return i
        return None

    def any_available(self) -> bool:
        return self.first_available() is not None

    def mark_exhausted(self, idx: int) -> None:
        self._exhausted.add(idx)

    def is_exhausted(self, idx: int) -> bool:
        return idx in self._exhausted


@dataclass
class Clients:
    stages12: genai.Client
    stage3_pool: Stage3Pool
    model_s1: str
    model_s2: str
    model_s3: str

    @classmethod
    def from_cfg(cls, cfg: dict[str, str]) -> "Clients":
        key12 = cfg["GEMINI_API_KEY_STAGES12"]
        key3 = cfg["GEMINI_API_KEY_STAGE3"]
        key3b = (cfg.get("GEMINI_API_KEY_STAGE3_BACKUP") or "").strip()
        pool_clients = [genai.Client(api_key=key3)]
        # Ignore a backup that's identical to the primary — they'd share the
        # same quota pool, so the "fallback" would be dead on arrival.
        if key3b and key3b != key3:
            pool_clients.append(genai.Client(api_key=key3b))
        return cls(
            stages12=genai.Client(api_key=key12),
            stage3_pool=Stage3Pool(pool_clients),
            model_s1=cfg["GEMINI_MODEL_STAGE1"],
            model_s2=cfg["GEMINI_MODEL_STAGE2"],
            model_s3=cfg["GEMINI_MODEL_STAGE3"],
        )


def _classify_error(exc: Exception) -> str:
    """Return 'permanent' or 'transient'. Default to transient when ambiguous
    — we'd rather retry uselessly than quit on a recoverable issue."""
    msg = f" {str(exc).lower()} "  # pad with spaces so " 400 " matches 'status: 400,'
    for sig in _PERMANENT_SIGNALS:
        if sig in msg:
            return "permanent"
    # Also treat specific exception types as permanent
    name = type(exc).__name__.lower()
    if "auth" in name or "permission" in name:
        return "permanent"
    return "transient"


def _backoff_for(attempt: int) -> int:
    idx = attempt - 1
    if idx < len(RETRY_BACKOFF):
        return RETRY_BACKOFF[idx]
    return MAX_BACKOFF_PLATEAU


def with_retry(
    fn: Callable[[], Any],
    on_retry: Callable[[int, int, Exception], None] | None = None,
) -> Any:
    """Call fn() and retry on transient errors with exponential backoff.

    - Retries up to MAX_ATTEMPTS times (effectively forever for human timescales).
    - On a PERMANENT error, raises PermanentLLMError immediately (no retry).
    - on_retry callback is called before each sleep with (attempt, sleep_s, exc).
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except PermanentLLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            kind = _classify_error(exc)
            if kind == "permanent":
                raise PermanentLLMError(
                    f"Permanent error from Google GenAI: {exc}\n"
                    f"This usually means a bad API key, an unknown model name, "
                    f"or a malformed request. Retrying won't help — fix the "
                    f"config (~/.config/clevernotes/config.env) and rerun."
                ) from exc
            if attempt >= MAX_ATTEMPTS:
                # We've retried 500 times. Something is very wrong. Surface it.
                raise
            sleep_s = _backoff_for(attempt)
            if on_retry:
                try:
                    on_retry(attempt, sleep_s, exc)
                except Exception:  # noqa: BLE001
                    pass  # never let UI callback break the retry loop
            time.sleep(sleep_s)


def image_part(path: Path) -> types.Part:
    return types.Part.from_bytes(data=path.read_bytes(), mime_type="image/png")


def generate_text(
    client: genai.Client,
    model: str,
    parts: list[Any],
    on_retry: Callable[[int, int, Exception], None] | None = None,
) -> str:
    def _call() -> str:
        resp = client.models.generate_content(model=model, contents=parts)
        return resp.text or ""

    return with_retry(_call, on_retry=on_retry)


def new_chat(client: genai.Client, model: str):
    return client.chats.create(model=model)


def chat_send(
    chat,
    parts: list[Any],
    on_retry: Callable[[int, int, Exception], None] | None = None,
) -> str:
    def _call() -> str:
        resp = chat.send_message(parts)
        return resp.text or ""

    return with_retry(_call, on_retry=on_retry)


class Stage3Chat:
    """Multi-turn chat for Stage 3 notes generation with automatic key
    failover. Owns a chat session bound to the currently-active client in a
    `Stage3Pool`. On daily-quota exhaustion (or a stubborn per-minute rate
    limit) it switches to the next non-exhausted client, replaying the
    existing chat history so the new session keeps cross-group context.

    One `Stage3Chat` per pptx/pdf file — history is scoped to that file, by
    design. The pool's exhaustion state persists across files.
    """

    def __init__(self, pool: Stage3Pool, model: str):
        start = pool.first_available()
        if start is None:
            raise PermanentLLMError(
                "All stage-3 API keys are already marked exhausted for today. "
                "Rerun `clevernotes` later — finished groups are preserved."
            )
        self._pool = pool
        self._model = model
        self._idx = start
        self._chat = pool.clients[start].chats.create(model=model, history=[])

    @property
    def active_idx(self) -> int:
        return self._idx

    def send(
        self,
        parts: list[Any],
        on_retry: Callable[[int, int, Exception], None] | None = None,
        on_switch: Callable[[int, str], None] | None = None,
    ) -> str:
        """Send a message; on quota issues, transparently switch clients.

        on_retry(attempt, sleep_s, exc): same contract as with_retry.
        on_switch(new_idx, reason): called right after a client switch lands.
          reason is "daily quota" or "persistent rate limit".
        """
        total_attempt = 0
        attempt_on_current = 0
        while True:
            total_attempt += 1
            attempt_on_current += 1
            try:
                resp = self._chat.send_message(parts)
                return resp.text or ""
            except Exception as exc:  # noqa: BLE001
                kind = _classify_error(exc)
                if kind == "permanent":
                    raise PermanentLLMError(
                        f"Permanent error from Google GenAI: {exc}\n"
                        f"This usually means a bad API key, an unknown model name, "
                        f"or a malformed request. Retrying won't help — fix the "
                        f"config (~/.config/clevernotes/config.env) and rerun."
                    ) from exc
                if total_attempt >= MAX_ATTEMPTS:
                    raise

                msg_lc = str(exc).lower()
                daily = any(s in msg_lc for s in _DAILY_QUOTA_SIGNALS)
                rate_like = any(s in msg_lc for s in _RATE_LIKE_SIGNALS)
                persistent_rate = (
                    rate_like and attempt_on_current >= STAGE3_SWITCH_AFTER_ATTEMPTS
                )

                if daily or persistent_rate:
                    if daily:
                        # Day-level exhaustion is durable until Google resets
                        # at midnight Pacific — mark so later files don't
                        # waste attempts on this key.
                        self._pool.mark_exhausted(self._idx)
                    if self._advance():
                        attempt_on_current = 0
                        if on_switch:
                            try:
                                on_switch(
                                    self._idx,
                                    "daily quota" if daily else "persistent rate limit",
                                )
                            except Exception:  # noqa: BLE001
                                pass
                        continue  # retry immediately on the new client
                    # _advance() failed — no other key to try.
                    if daily and not self._pool.any_available():
                        raise PermanentLLMError(
                            "All stage-3 API keys have hit their daily quota. "
                            "Free-tier Gemini resets around midnight Pacific. "
                            "Rerun `clevernotes` later — finished groups are "
                            "preserved."
                        ) from exc
                    # persistent_rate with only one key available — fall
                    # through to normal backoff and keep trying.

                sleep_s = _backoff_for(attempt_on_current)
                if on_retry:
                    try:
                        on_retry(total_attempt, sleep_s, exc)
                    except Exception:  # noqa: BLE001
                        pass
                time.sleep(sleep_s)

    def _advance(self) -> bool:
        """Switch to the next non-exhausted client, replaying existing
        chat history so the new session keeps cross-group context. Returns
        True if a switch happened, False if there's nowhere to go.
        """
        try:
            history = self._chat.get_history()
        except Exception:  # noqa: BLE001
            # Defensive: if the google-genai version here doesn't expose
            # get_history, drop history rather than crash — context loss is
            # preferable to the whole file aborting.
            history = []

        n = self._pool.size()
        for offset in range(1, n + 1):
            j = (self._idx + offset) % n
            if j == self._idx:
                break
            if self._pool.is_exhausted(j):
                continue
            self._idx = j
            self._chat = self._pool.clients[j].chats.create(
                model=self._model, history=history
            )
            return True
        return False
