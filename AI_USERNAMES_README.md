# AI Themed Username Generator

Describe the vibe you want in plain English — a word or a full
sentence — and get back real, single-word usernames that match. Uses
Google's free Gemini API (no credit card, no trial expiry).

Examples of themes you can type:
- `anime based usernames`
- `cyberpunk hacker names`
- `cute pastel usernames for a kids app`
- `dark fantasy villain names`
- `nature and outdoorsy usernames`

## 1. Get a free API key (5 minutes, no credit card)
1. Go to **https://aistudio.google.com/apikey**
2. Sign in with any Google account
3. Click **Create API key**
4. Copy the key (starts with `AIza...`)

## 2. Install the one dependency
```
pip install google-genai --break-system-packages
```
(Drop `--break-system-packages` if you're using a virtual environment.)

## 3. Set your key — recommended: use the .env file
This is the easiest way, especially if you're sharing this folder with
teammates — everyone keeps their own key locally, nobody has to
remember `export` commands, and it never gets accidentally shared if
you're using git (the included `.gitignore` protects it).

1. In this folder, copy **`.env.example`** to a new file named **`.env`**
2. Open `.env` and paste your key after `GEMINI_API_KEY=`
3. Save. That's it — the script loads it automatically every run.

```
# .env
GEMINI_API_KEY=AIza...your key here...
```

**Sharing this with teammates:** send them the whole folder (or push it
to git — `.env` is gitignored so your personal key never gets included).
Each person just copies `.env.example` to their own `.env` and drops in
their own free key. No one has to touch `export` or environment
variables at all.

**Alternative — environment variable**, if you'd rather not use a file:
```
export GEMINI_API_KEY="AIza...your key..."        # Mac/Linux
setx GEMINI_API_KEY "AIza...your key..."           # Windows PowerShell
```
An explicit environment variable or `--api-key` flag always overrides
the `.env` file, so this still works if you want it for one-off runs.

## 4. Run it

**Interactive:**
```
python3 ai_usernames.py
```
Asks you: the theme, how many usernames, prefix, suffix, where to save.

**Scripted:**
```
python3 ai_usernames.py --theme "anime based usernames" --count 100 --prefix "@" --suffix "_2026" --out-dir .
```

**Zero prompts:**
```
python3 ai_usernames.py -y --theme "cyberpunk hacker names" --count 50
```

## Prefix and suffix
Every username can get a fixed prefix and/or suffix, applied after the
AI generates the base word — so the "single word only" validation still
happens on the clean word first, then the prefix/suffix is added.

Interactive mode gives you a menu: `@`, `#`, `$`, none, or custom text
(e.g. `Pro`, `_2026`, `xX`). Scripted mode uses `--prefix` and `--suffix`:
```
python3 ai_usernames.py --theme "anime usernames" --prefix "@" --suffix "_2026"
```
Result: `@Kaito_2026`, `@Sakura_2026`, `@Ronin_2026`, ...

If your prefix/suffix starts with a dash (e.g. `-Pro`), use the
equals-sign form so it isn't mistaken for a flag:
```
python3 ai_usernames.py --theme "anime usernames" --suffix="-Pro"
```

## What you get
A CSV with one username per line, e.g. for "anime based usernames"
with `@` prefix and no suffix:
```
@Kaito
@Sakura
@Ronin
@Yokai
@Haruki
@Miko
```
No two words merged, no camelCase (`ShadowBlade` style is rejected), no
random IDs — every base word is checked in Python after the AI
responds, so the "single word only" rule is actually enforced, not
just requested. Prefix/suffix are applied afterward and don't affect
that validation.

## How the single-word guarantee works
The AI is instructed to return one word per line, but AI models don't
always follow formatting rules perfectly — especially under load. So
every line that comes back is independently validated:
- Rejected if it has a space, hyphen, or underscore
- Rejected if it's camelCase/PascalCase (catches "ShadowBlade"-style merges)
- Rejected if it has digits or symbols
- Duplicates (case-insensitive) are dropped automatically

Anything that fails is silently discarded rather than "fixed" by
guessing — so what ends up in your CSV is trustworthy for real users.

## Rate limits (free tier, as of 2026)
Roughly 10 requests/minute and a few hundred requests/day on
`gemini-2.5-flash`. The script batches ~50-60 words per request and
waits between calls automatically, so you shouldn't hit limits for
typical use (tens to low hundreds of usernames). For very large counts
(thousands+), expect it to take a while — it's pacing itself
deliberately to stay within the free tier.

## Options
| Flag | Description |
|---|---|
| `--theme TEXT` | Describe the username style (any length) |
| `--count N` | How many usernames to generate (default: 50) |
| `--prefix TEXT` | Fixed prefix for every username, e.g. `@`, `#`, `$` (default: none) |
| `--suffix TEXT` | Fixed suffix for every username, e.g. `_2026`, `Pro` (default: none) |
| `--out-dir PATH` | Folder to save CSV in (default: Desktop) |
| `--model NAME` | Gemini model to use (default: `gemini-3.5-flash-lite`) |
| `--api-key KEY` | Your Gemini key (or set `GEMINI_API_KEY` env var) |
| `-y`, `--yes` | Skip all prompts, use defaults |

## If Google retires the default model again
Google periodically renames/retires Gemini models (this already happened
once between when this script was written and when it was first run —
`gemini-2.5-flash` was closed to new API keys). The script now handles
this automatically: if the model it's using returns a "model not found"
error, it silently tries the next known-good model in its fallback list
(currently `gemini-3.1-flash-lite`, `gemini-3.5-flash`,
`gemini-2.5-flash-lite`, `gemini-2.5-flash`, in that order) and sticks
with whichever one works for the rest of the run.

If Google eventually retires *all* of those too, you'll get a clear
error message pointing you to the current model list at
ai.google.dev/gemini-api/docs/models — just pass the current model
name with `--model`.

## A note on repeated/large runs
If you ask for thousands of usernames on a very narrow theme, the
model may eventually run out of genuinely distinct real words for that
theme and start repeating. The script detects this (4 batches in a row
with zero new unique words) and stops early with a clear message
rather than looping forever — try a broader theme or a lower count if
that happens.

## Cost
$0. Google's Gemini free tier requires no credit card and doesn't
expire. If you ever exceed the free daily quota, the script will show
a rate-limit error — just wait for it to reset (resets daily) or
enable billing on your Google Cloud project for higher limits.
