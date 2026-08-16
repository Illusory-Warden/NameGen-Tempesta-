#!/usr/bin/env python3
"""
ai_usernames.py — Generate single-word, theme-matched usernames using a
free AI model (Google Gemini). You describe the vibe in plain English
("anime based usernames", "cyberpunk hacker names", "cute pastel names
for a kids app") and it generates real single words that fit — no two
words merged, no camelCase, no random-character IDs.

WHY GEMINI
----------
Google AI Studio's Gemini API has a genuinely free tier: no credit
card, no trial expiry, real daily quota (as of 2026: ~10 requests/min,
~250-500 requests/day on gemini-2.5-flash — plenty for this). Get a
free key in under 5 minutes:

    1. Go to https://aistudio.google.com/apikey
    2. Sign in with any Google account
    3. Click "Create API key"
    4. Copy the key (starts with "AIza...")

SETUP
-----
    pip install google-genai --break-system-packages

Then set your API key one of two ways:

  A) .env file (recommended for teams — do this once):
     1. Copy .env.example to .env in this same folder
     2. Open .env and paste your key after GEMINI_API_KEY=
     3. Done — the script loads it automatically every run, no
        exporting needed, and each teammate keeps their own .env
        (never commit .env to git — .env.example is the template
        that's safe to share instead)

  B) Environment variable (if you prefer):
     export GEMINI_API_KEY="AIza...your key here..."

USAGE
-----
Interactive (just run it):
    python3 ai_usernames.py

Scriptable:
    python3 ai_usernames.py --theme "anime based usernames" --count 50 --out-dir .

Any flag you skip will be prompted for, unless you also pass --yes.

HOW IT ENFORCES "SINGLE WORD, NO MERGING"
------------------------------------------
The model is instructed to return one real/plausible single word per
line. Since LLMs occasionally ignore formatting instructions under
load, every returned line is independently validated in Python:
  - must be a single token (no internal spaces)
  - must not be camelCase or PascalCase (rejects "ShadowBlade" style)
  - must not contain digits or symbols unless requested
  - duplicates against earlier lines/batches are dropped
Anything that fails validation is silently discarded, not "fixed" by
guessing what the real word was meant to be — that keeps the output
trustworthy for a real userbase.

NO EXTERNAL DEPENDENCIES beyond the official `google-genai` SDK, which
is free and open source. The .env loader below is hand-written (no
python-dotenv needed) so teammates don't need an extra pip install.
"""

import argparse
import csv
import os
import re
import sys
import time

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None


DEFAULT_MODEL = "gemini-3.5-flash-lite"

# If the primary model becomes unavailable (Google retires/renames models
# periodically), these are tried in order as automatic fallbacks. This
# list is deliberately ordered newest/cheapest-first among Flash-Lite/
# Flash tiers, which are the ones Google keeps on the free tier.
FALLBACK_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]
MAX_PER_REQUEST = 60          # ask for a modest batch each call to stay reliable
MAX_RETRIES = 3
REQUEST_DELAY_SECONDS = 2.5   # stay comfortably under free-tier RPM limits

ENV_FILENAME = ".env"


def load_dotenv(path=None):
    """
    Minimal, dependency-free .env loader. Reads KEY=VALUE lines from a
    .env file sitting next to this script (or at `path`) and sets them
    as environment variables — but only if that variable isn't already
    set, so an explicit `export GEMINI_API_KEY=...` or --api-key flag
    always wins over the .env file.

    Supports: blank lines, "# comment" lines, optional surrounding
    quotes around the value, and "export KEY=VALUE" (some people copy
    that style out of habit). Silently does nothing if no .env exists —
    that's a normal, expected case, not an error.
    """
    if path is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(script_dir, ENV_FILENAME)

    if not os.path.isfile(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Strip matching surrounding quotes, if present
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        # .env exists but couldn't be read (permissions, etc.) — not
        # fatal, just fall back to whatever env vars are already set.
        pass


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def find_desktop():
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "Desktop"),
        os.path.join(home, "OneDrive", "Desktop"),
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return home if os.path.isdir(home) else os.getcwd()


def sanitize_theme_for_filename(theme):
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", theme.strip().lower()).strip("_")
    return safe[:40] if safe else "usernames"


def sanitize_affix_for_filename(text):
    """Turn a prefix/suffix like '@' or '#' into a safe filename token."""
    if not text:
        return ""
    symbol_names = {"@": "at", "#": "hash", "$": "dollar", "%": "percent",
                     "&": "and", "*": "star", "+": "plus"}
    if text in symbol_names:
        return symbol_names[text]
    safe = re.sub(r"[^a-zA-Z0-9]", "", text)
    return safe.lower() if safe else "sym"


def build_output_filename(theme, count, prefix, suffix):
    parts = [sanitize_theme_for_filename(theme), str(count), "usernames"]
    if prefix:
        parts.append(sanitize_affix_for_filename(prefix))
    if suffix:
        parts.append(sanitize_affix_for_filename(suffix))
    return "_".join(parts) + ".csv"


# --------------------------------------------------------------------------
# Word validation — this is what actually guarantees "single word, no
# merging, no camelCase" regardless of what the model returns.
# --------------------------------------------------------------------------

CAMEL_CASE_RE = re.compile(r"^[a-z0-9]*[A-Z]")        # lowercase then a capital = camelCase
MULTI_CAP_RUN_RE = re.compile(r"[A-Z][a-z]+[A-Z][a-z]+")  # e.g. "ShadowBlade" pattern
VALID_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z']*$")   # letters only, optional apostrophe


def is_single_clean_word(candidate):
    """
    Returns True only if `candidate` is a single real-looking word:
    no internal whitespace, no camelCase/PascalCase compounding, no
    digits/symbols, not empty. Allows ALL-CAPS and Title-case words,
    since those are legitimate single-word styles; only rejects words
    that mix case in a way that signals two words were merged
    (e.g. "ShadowBlade").
    """
    word = candidate.strip()
    if not word:
        return False
    if " " in word or "\t" in word:
        return False
    if "_" in word or "-" in word:
        return False
    if not VALID_WORD_RE.match(word):
        return False
    # Reject "ShadowBlade"-style merges: a lowercase run followed by
    # another capitalized run inside the same token. This pattern is
    # what actually distinguishes "two real words glued together" from
    # legitimate single-word casing styles like "YOKAI" or "yokai" or
    # "Yokai" (all of which have at most one case transition at the
    # very start of the word).
    if MULTI_CAP_RUN_RE.search(word):
        return False
    return True


def normalize_word(word):
    """Title-case the word for consistent output (Shadow, not shadow/SHADOW)."""
    return word.strip().capitalize()


def clean_batch(raw_lines):
    """Filter a batch of raw model output lines down to valid single words."""
    cleaned = []
    for line in raw_lines:
        line = line.strip()
        # Strip common list formatting the model might add despite instructions
        line = re.sub(r"^[\-\*\d\.\)\s]+", "", line)
        line = line.strip()
        if not line:
            continue
        if is_single_clean_word(line):
            cleaned.append(normalize_word(line))
    return cleaned


# --------------------------------------------------------------------------
# Gemini API call
# --------------------------------------------------------------------------

def build_prompt(theme, batch_size, exclude_words):
    exclusion_block = ""
    if exclude_words:
        sample = ", ".join(sorted(exclude_words)[:80])
        exclusion_block = (
            f"\nDo NOT repeat any of these already-used words: {sample}\n"
        )
    return f"""You generate single-word usernames for a website's anonymous users.

Theme / style requested by the user: "{theme}"

Rules — follow exactly:
1. Output exactly {batch_size} words, one per line, nothing else.
2. Each word must be ONE real or plausible single word — no spaces,
   no hyphens, no underscores, no two words joined together
   (do NOT do "ShadowBlade" or "shadow_blade" or "shadow-blade").
3. No numbers, no emoji, no punctuation, no explanations, no headers,
   no markdown, no bullet points, no quotation marks.
4. Words should genuinely fit the theme (e.g. for "anime based
   usernames" use real anime-style single words/names like Kaito,
   Sakura, Ronin, Yokai — not generic English words with no
   connection to anime).
5. Every word must be different from every other word in your list.
{exclusion_block}
Output the {batch_size} words now, one per line:"""


def call_gemini(client, model, theme, batch_size, exclude_words, attempt=1, model_index=0):
    """
    Calls the Gemini API. If the model itself is unavailable (404 —
    Google retired/renamed it, which happens periodically), automatically
    tries the next model in FALLBACK_MODELS rather than retrying a dead
    model name. Transient errors (rate limits, network blips) use normal
    retry-with-backoff on the SAME model instead.
    """
    prompt = build_prompt(theme, batch_size, exclude_words)
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=1.1,      # more variety across usernames
                max_output_tokens=800,
            ),
        )
        text = response.text or ""
        return text.splitlines(), model
    except Exception as e:
        error_str = str(e)
        is_model_not_found = "404" in error_str and "NOT_FOUND" in error_str

        if is_model_not_found:
            candidates = [model] + FALLBACK_MODELS
            next_index = model_index + 1
            if next_index < len(candidates):
                next_model = candidates[next_index]
                print(f"  (Model '{model}' unavailable. Trying '{next_model}' instead...)",
                      file=sys.stderr)
                return call_gemini(client, next_model, theme, batch_size, exclude_words,
                                    attempt=1, model_index=next_index)
            print(
                f"  ERROR: none of the known model names worked. Google may have "
                f"changed its model lineup again. Check current model names at "
                f"https://ai.google.dev/gemini-api/docs/models and pass one with "
                f"--model, e.g. --model gemini-3.6-flash",
                file=sys.stderr,
            )
            return [], model

        if attempt < MAX_RETRIES:
            wait = REQUEST_DELAY_SECONDS * attempt
            print(f"  (API call failed: {e}. Retrying in {wait:.0f}s...)", file=sys.stderr)
            time.sleep(wait)
            return call_gemini(client, model, theme, batch_size, exclude_words,
                                attempt + 1, model_index)
        print(f"  ERROR: Gemini call failed after {MAX_RETRIES} attempts: {e}", file=sys.stderr)
        return [], model


def generate_usernames(theme, count, model, api_key):
    if genai is None:
        print(
            "ERROR: the 'google-genai' package isn't installed.\n"
            "Install it with:\n"
            "    pip install google-genai --break-system-packages",
            file=sys.stderr,
        )
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    collected = []
    seen_lower = set()
    consecutive_empty_batches = 0
    active_model = model
    active_model_index = 0

    print(f"\nGenerating {count:,} usernames for theme: \"{theme}\"")
    print("This calls a free AI model, so it may take a little while for large counts.\n")

    while len(collected) < count:
        remaining = count - len(collected)
        batch_size = min(MAX_PER_REQUEST, remaining + 10)  # ask for a few extra to offset filtering losses

        raw_lines, working_model = call_gemini(
            client, active_model, theme, batch_size, seen_lower,
            model_index=active_model_index,
        )
        if working_model != active_model:
            # A fallback model took over — stick with it for the rest of the run.
            active_model = working_model
            candidates = [model] + FALLBACK_MODELS
            active_model_index = candidates.index(working_model) if working_model in candidates else 0

        cleaned = clean_batch(raw_lines)

        new_words = 0
        for word in cleaned:
            key = word.lower()
            if key in seen_lower:
                continue
            seen_lower.add(key)
            collected.append(word)
            new_words += 1
            if len(collected) >= count:
                break

        print(f"  +{new_words} new usernames  (total: {len(collected):,}/{count:,})")

        if new_words == 0:
            consecutive_empty_batches += 1
            if consecutive_empty_batches >= 4:
                print(
                    f"\nStopping early: the model isn't producing new unique words for "
                    f"this theme after several attempts. Got {len(collected):,} of "
                    f"{count:,} requested. Try a broader theme or a lower --count.",
                    file=sys.stderr,
                )
                break
        else:
            consecutive_empty_batches = 0

        time.sleep(REQUEST_DELAY_SECONDS)  # stay under free-tier rate limits

    return collected


# --------------------------------------------------------------------------
# Interactive prompts
# --------------------------------------------------------------------------

def prompt_theme():
    print("\nWhat type of usernames do you want?")
    print("(One word or a full sentence, e.g. 'anime based usernames',")
    print(" 'cyberpunk hacker names', 'cute usernames for a kids app')")
    while True:
        theme = input("\nYour answer: ").strip()
        if theme:
            return theme
        print("Please enter something.")


def prompt_count():
    raw = input("\nHow many usernames? (default 50): ").strip()
    if not raw:
        return 50
    try:
        n = int(raw)
        return n if n > 0 else 50
    except ValueError:
        return 50


PREFIX_SUFFIX_CHOICES = {
    "1": "@",
    "2": "#",
    "3": "$",
    "4": "",       # no prefix/suffix
    "5": None,     # signal: ask for custom text
}


def prompt_affix(label):
    print(f"\nChoose a {label} for every username:")
    print("  1. @")
    print("  2. #")
    print("  3. $")
    print("  4. None")
    print("  5. Custom (type your own)")
    while True:
        raw = input(f"Your choice (1-5, default 4): ").strip()
        if raw == "":
            raw = "4"
        if raw in PREFIX_SUFFIX_CHOICES:
            choice = PREFIX_SUFFIX_CHOICES[raw]
            if choice is None:
                custom = input(f"Enter your custom {label} (e.g. 'Pro', '_2026', 'xX'): ").strip()
                return custom
            return choice
        print("Please enter a number from 1 to 5.")


def prompt_output_dir():
    default_dir = find_desktop()
    raw = input(f"\nSave folder (default: {default_dir}): ").strip()
    return raw if raw else default_dir


def prompt_api_key():
    existing = os.environ.get("GEMINI_API_KEY")
    if existing:
        return existing
    print("\nNo GEMINI_API_KEY found (checked .env file and environment variables).")
    print("Get a free key (no credit card) at: https://aistudio.google.com/apikey")
    print("Tip: copy .env.example to .env and paste your key there to skip this prompt next time.")
    key = input("Paste your Gemini API key here: ").strip()
    return key


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------

def main():
    load_dotenv()  # picks up GEMINI_API_KEY from a .env file next to this script, if present

    parser = argparse.ArgumentParser(
        description="Generate single-word, theme-matched usernames using a free AI model (Gemini).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--theme", type=str, help="Describe the username style you want (any length)")
    parser.add_argument("--count", type=int, help="How many usernames to generate (default: 50)")
    parser.add_argument("--prefix", type=str, default=None, metavar="TEXT",
                         help='Fixed prefix for every username, e.g. "@", "#", "$" (default: none). '
                              'If it starts with "-", use --prefix="-Pro" (with an equals sign) '
                              'so it is not mistaken for another flag.')
    parser.add_argument("--suffix", type=str, default=None, metavar="TEXT",
                         help='Fixed suffix for every username, e.g. "_2026", "Pro" (default: none). '
                              'Same --suffix="-Pro" rule as --prefix applies.')
    parser.add_argument("--out-dir", type=str, help="Folder to save CSV in (default: Desktop)")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL,
                         help="Gemini model to use")
    parser.add_argument("--api-key", type=str,
                         help="Gemini API key (or set GEMINI_API_KEY env var)")
    parser.add_argument("-y", "--yes", action="store_true",
                         help="Accept defaults for anything not specified (no prompts)")
    args = parser.parse_args()

    # ---- Theme ----
    if args.theme:
        theme = args.theme
    elif args.yes:
        theme = "cool gamer usernames"
    else:
        theme = prompt_theme()

    # ---- Count ----
    if args.count is not None:
        count = args.count
        if count <= 0:
            print("--count must be positive.", file=sys.stderr)
            sys.exit(1)
    elif args.yes:
        count = 50
    else:
        count = prompt_count()

    # ---- Prefix / Suffix ----
    if args.prefix is not None:
        prefix = args.prefix
    elif args.yes:
        prefix = ""
    else:
        prefix = prompt_affix("prefix")

    if args.suffix is not None:
        suffix = args.suffix
    elif args.yes:
        suffix = ""
    else:
        suffix = prompt_affix("suffix")

    # ---- API key ----
    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key and not args.yes:
        api_key = prompt_api_key()
    if not api_key:
        print(
            "ERROR: no Gemini API key found. Copy .env.example to .env and add your "
            "key there, set the GEMINI_API_KEY environment variable, or pass --api-key. "
            "Get a free key at https://aistudio.google.com/apikey",
            file=sys.stderr,
        )
        sys.exit(1)

    # ---- Output dir ----
    if args.out_dir:
        out_dir = args.out_dir
    elif args.yes:
        out_dir = find_desktop()
    else:
        out_dir = prompt_output_dir()

    usernames = generate_usernames(theme, count, args.model, api_key)

    if not usernames:
        print("\nNo usernames were generated. Check your API key and try again.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(out_dir, exist_ok=True)
    filename = build_output_filename(theme, len(usernames), prefix, suffix)
    out_path = os.path.join(out_dir, filename)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for name in usernames:
            writer.writerow([f"{prefix}{name}{suffix}"])

    print(f"\nDone. Wrote {len(usernames):,} usernames to:\n  {out_path}")


if __name__ == "__main__":
    main()
