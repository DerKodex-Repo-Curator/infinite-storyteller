# The Infinite Storyteller

**Offline fantasy tabletop-style text RPG** for 1–4 players on one computer.

Python owns the rules: character sheets, inventory, HP, dice, dying checks, and a fixed
enemy encounter roster. A **local LLM via [Ollama](https://ollama.com)** acts as the
Game Master — helping forge characters and narrating outcomes. After you download a
model once, the game runs with **no internet**.

> Have fun. Type foolish plans. Roll badly. Ask the GM weird questions.  
> And if you improve something — please share it back with the world.

---

## Features

- **Terminal edition** (`game.py`) — classic keyboard adventure
- **Web UI** (`gui_web.py`) — local browser app at `http://127.0.0.1:8765`
- **macOS helpers** — `Infinite Storyteller.app` and `Play Infinite Storyteller.command`
- **1–4 players** hot-seat: everyone locks in an action, then one shared round resolves
- **Ask GM** — free clarifying questions (does not spend a turn)
- **Adventure length** — Short / Standard / Long with act pacing toward a finale
- **Death & dying** — unconscious at 0 HP, dying checks, stabilize, heal, mercy rule
- **Tracked fights** — fixed enemy HP roster so battles don’t become “endless wolves”
- **Model picker** — choose any model installed in Ollama (recommended: `qwen2.5:14b` / `llama3.1:8b`)
- **Stdlib only** — no `pip install` required

---

## Requirements

| Need | Notes |
|------|--------|
| **Python 3.9+** | Uses the standard library only |
| **[Ollama](https://ollama.com)** | Local LLM runtime (`localhost:11434`) |
| **~5–10+ GB disk** | For at least one chat model |
| **RAM** | 8B models are friendlier on 16 GB; 14B is nicer on 24 GB+ (e.g. Apple Silicon) |

Supported OSes: macOS, Linux, and Windows (via Python + Ollama; the `.app` is macOS-only).

---

## Quick start

### 1. Install Ollama and pull a model

```bash
# Install from https://ollama.com then:
ollama serve          # or open the Ollama app on macOS
ollama pull qwen2.5:14b     # recommended default (~9 GB)
ollama pull llama3.1:8b     # optional faster backup (~4.9 GB)
ollama list                 # confirm they're installed
```

You can use **any other Ollama model**. The web UI lists everything installed locally.
Point the game at a model by:

- selecting it in the **Local AI model** dropdown (web), or  
- `python3 game.py your-model-name` (terminal)

### 2. Get this project

```bash
git clone https://github.com/brantley-creator/infinite-storyteller.git
cd infinite-storyteller
```

No virtualenv or dependencies required. (`requirements.txt` is empty on purpose.)

### 3. Play — terminal version

```bash
# Make sure Ollama is running first
python3 game.py
# or pick a model:
python3 game.py llama3.1:8b
```

You’ll choose party size (1–4), forge heroes, pick a world and adventure length, then type actions in plain English. Type `help` anytime.

### 4. Play — web version

```bash
python3 gui_web.py
```

Your browser should open `http://127.0.0.1:8765`. If not, visit that URL manually.

**macOS shortcuts** (keep these files *inside* the project folder):

| Launcher | What it does |
|----------|----------------|
| `Infinite Storyteller.app` | Starts Ollama (if possible) + `gui_web.py` |
| `Play Infinite Storyteller.command` | Same, but in Terminal (best if Gatekeeper blocks the `.app`) |

If macOS refuses the `.app` (especially offline): **Right-click → Open → Open**, or use the `.command` file.

---

## How it works

| Piece | Owner | Notes |
|-------|--------|--------|
| Ability scores, skills, AC, HP | Python | Always visible on sheets |
| Inventory | Python | GM may add/remove only via `STATUS` JSON |
| Dice | **Python only** | Printed: roll + mods vs difficulty / armor |
| Enemy fights | **Python roster** | Fixed foes + HP; no endless reinforcements |
| Story / narration | Local LLM | Proposes checks + describes outcomes |

**Turn flow**

1. You describe what a hero does (or lock in each hero in party mode)  
2. AI proposes a check (or says none needed) as JSON  
3. Python rolls and shows the math  
4. AI narrates based on that result  
5. Python applies HP / inventory / location / encounter updates  

**Party play (2–4)** — everyone **locks in** first; then one shared GM reply.  
**Pass** skips that hero for the round. **Ask GM** never spends a turn.

---

## Pointing the game at your local models

Ollama must be reachable at **`http://localhost:11434`** (the default).

| Goal | How |
|------|-----|
| See installed models | `ollama list` |
| Pull a new model | `ollama pull mistral` (example) |
| Use it in the web UI | Refresh the page → pick it in **Local AI model** / **Model for next reply** |
| Use it in the terminal | `python3 game.py mistral` |
| Change the code default | Edit `DEFAULT_MODEL` in `game.py` (and optionally `RECOMMENDED_MODELS`) |

The web UI’s `/api/status` endpoint discovers whatever Ollama has installed and fills the dropdown. Recommended models get friendlier labels; everything else still appears as “installed locally”.

Saves remember which model you were using.

---

## Saves

Games save as JSON under `./saves/` (created automatically; gitignored so your adventures stay private).

- Web: **Save** button  
- Terminal: `save`, `load`, `saves`

---

## Project layout

```
infinite-storyteller/
├── game.py              # Rules engine + Ollama client + CLI
├── gui_web.py           # Primary GUI (local web server)
├── gui.py               # Legacy Tk GUI (optional)
├── test_game.py         # Offline unit tests (no Ollama required)
├── Play Infinite Storyteller.command
├── Infinite Storyteller.app/
├── saves/               # Your save files (not committed)
├── requirements.txt     # Empty — stdlib only
├── LICENSE
└── README.md
```

---

## Testing

```bash
python3 test_game.py
```

Covers dying checks, party planning, encounter tracking, dice parsing, and API guards — with the LLM stubbed.

---

## Offline / travel tips

1. Pull models **while you still have internet**  
2. Start **Ollama** before the game  
3. On battery, prefer `llama3.1:8b` (or smaller) over a 14B model  
4. Keep the whole folder together — the macOS `.app` needs `gui_web.py` beside it  

---

## Contributing — please help make it better!

This started as a plane-ride RPG for friends and family. It’s intentionally simple so
others can hack on it.

Ideas we would love:

- Better encounter / boss design  
- More world seeds  
- Accessibility and UI polish  
- Windows packaging  
- Streaming narration  
- Optional sound or maps  
- Docs translations  

**How to contribute**

1. Fork the repo  
2. Create a branch (`git checkout -b feature/amazing-idea`)  
3. Keep the stdlib-only promise unless there’s a strong reason not to  
4. Add or update tests in `test_game.py` when you touch rules  
5. Open a Pull Request and tell us how to try it  

Bug reports and ideas via [GitHub Issues](https://github.com/brantley-creator/infinite-storyteller/issues) are very welcome.

---

## Have fun

You are the heroes. The dice are honest. The storyteller is a laptop that can’t connect to
the cloud at 30,000 feet — and that’s the point.

**Grab the keyboard. Tell a story. Invite a friend to play.**

---

## License

MIT — see [LICENSE](LICENSE). Use it, remix it, ship it on your next adventure.
