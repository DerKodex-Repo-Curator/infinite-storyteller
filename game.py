#!/usr/bin/env python3
"""
The Infinite Storyteller — offline fantasy text RPG.

Python owns character sheets, inventory, HP, and all dice rolls.
A local LLM (Ollama) helps with character creation and narrates outcomes.

  ollama pull qwen2.5:14b
  python3 game.py
"""

from __future__ import annotations

import json
import random
import re
import sys
import textwrap
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OLLAMA_HOST = "http://localhost:11434"
OLLAMA_URL = f"{OLLAMA_HOST}/api/generate"
DEFAULT_MODEL = "qwen2.5:14b"
# Friendly labels for models we recommend. Any other locally installed Ollama
# model can still be selected in the web UI / passed on the CLI.
RECOMMENDED_MODELS = {
    "qwen2.5:14b": "richer GM (slower, ~9 GB)",
    "llama3.1:8b": "faster backup (~4.9 GB)",
}
HISTORY_TURNS = 10
PROFICIENCY_BONUS = 2  # fixed level-1 party
SAVE_DIR = Path(__file__).resolve().parent / "saves"
SAVE_VERSION = 1

HELP_TEXT = """
┌─ COMMANDS ─────────────────────────────────────────────────
│ help / ?              Show this list
│ sheet [name]          Character sheet(s)
│ inventory / i         Party inventories
│ party / status        HP, AC, location, whose turn
│ look                  Re-describe the scene (uses an action)
│ ask <question>        Ask the Game Master a clarifying question (free — no turn)
│ history               Show recent story beats
│
│ use <name>            Switch whose turn it is
│ Name: <action>        Act as that hero this turn
│ pass / skip / next    Skip this hero's action this round (parties plan together)
│
│ save [slot]           Save game (default slot: quicksave)
│ load [slot]           Load a saved game
│ saves                 List save slots
│
│ roll d20              Free roll (also: roll 2d6+3, roll stealth)
│ quit / q              Quit (offers to save first)
│
│ Anything else         Treated as an in-world action for the
│                       active hero — plain English is fine.
└────────────────────────────────────────────────────────────
Tip: type help anytime. Saves live in ./saves/
""".strip()

ABILITIES = ("STR", "DEX", "CON", "INT", "WIS", "CHA")

SKILL_ABILITY = {
    "Athletics": "STR",
    "Acrobatics": "DEX",
    "Sleight of Hand": "DEX",
    "Stealth": "DEX",
    "Arcana": "INT",
    "History": "INT",
    "Investigation": "INT",
    "Nature": "INT",
    "Religion": "INT",
    "Animal Handling": "WIS",
    "Insight": "WIS",
    "Medicine": "WIS",
    "Perception": "WIS",
    "Survival": "WIS",
    "Deception": "CHA",
    "Intimidation": "CHA",
    "Performance": "CHA",
    "Persuasion": "CHA",
}

ARCHETYPE_DEFAULTS = {
    "Fighter": {
        "hit_die": 10,
        "skills": ["Athletics", "Intimidation"],
        "gear": ["longsword", "shield", "chain mail", "explorer's pack"],
        "ac_base": 16,
    },
    "Rogue": {
        "hit_die": 8,
        "skills": ["Stealth", "Sleight of Hand", "Perception"],
        "gear": ["shortsword", "shortbow", "arrows (20)", "leather armor", "thieves' tools"],
        "ac_base": 13,  # + DEX applied later in creation
    },
    "Wizard": {
        "hit_die": 6,
        "skills": ["Arcana", "Investigation"],
        "gear": ["quarterstaff", "spellbook", "component pouch", "scholar's pack"],
        "ac_base": 10,
    },
    "Cleric": {
        "hit_die": 8,
        "skills": ["Medicine", "Insight", "Religion"],
        "gear": ["mace", "shield", "scale mail", "holy symbol", "priest's pack"],
        "ac_base": 14,
    },
    "Ranger": {
        "hit_die": 10,
        "skills": ["Survival", "Perception", "Stealth"],
        "gear": ["longbow", "arrows (20)", "shortsword", "leather armor", "explorer's pack"],
        "ac_base": 13,
    },
    "Barbarian": {
        "hit_die": 12,
        "skills": ["Athletics", "Intimidation"],
        "gear": ["greataxe", "javelins (4)", "explorer's pack"],
        "ac_base": 10,  # unarmored: 10 + DEX + CON applied in creation
    },
}


# ---------------------------------------------------------------------------
# Dice & math (Python is the source of truth)
# ---------------------------------------------------------------------------

def ability_mod(score: int) -> int:
    return (int(score) - 10) // 2


def mod_str(mod: int) -> str:
    return f"+{mod}" if mod >= 0 else str(mod)


def roll_die(sides: int) -> int:
    return random.randint(1, sides)


def roll_dice(expr: str) -> Tuple[int, str]:
    """Roll NdM with any number of trailing +/- bonuses. Returns (total, detail)."""
    expr = expr.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d*)d(\d+)((?:[+-]\d+)*)", expr)
    if not m:
        # plain integer damage fallback
        try:
            val = int(expr)
            return val, str(val)
        except ValueError:
            return 0, f"invalid:{expr}"
    n = int(m.group(1) or "1")
    sides = int(m.group(2))
    bonus = sum(int(x) for x in re.findall(r"[+-]\d+", m.group(3) or ""))
    rolls = [roll_die(sides) for _ in range(max(1, n))]
    total = sum(rolls) + bonus
    detail = f"{'+'.join(map(str, rolls))}"
    if bonus:
        detail += f"{mod_str(bonus)}"
    detail += f"={total}"
    return total, detail


def damage_die_only(expr: str) -> str:
    """Strip LLMs that bake modifiers into damage_die (e.g. '1d8+2' → '1d8')."""
    expr = str(expr or "1d6").strip().lower().replace(" ", "")
    m = re.match(r"(\d*)d(\d+)", expr)
    if not m:
        return "1d6"
    n = m.group(1) or "1"
    return f"{n}d{m.group(2)}"


def d20_roll(advantage: bool = False, disadvantage: bool = False) -> Tuple[int, str]:
    if advantage and not disadvantage:
        a, b = roll_die(20), roll_die(20)
        pick = max(a, b)
        return pick, f"d20 adv({a},{b})={pick}"
    if disadvantage and not advantage:
        a, b = roll_die(20), roll_die(20)
        pick = min(a, b)
        return pick, f"d20 dis({a},{b})={pick}"
    r = roll_die(20)
    return r, f"d20={r}"


# ---------------------------------------------------------------------------
# Character & party state
# ---------------------------------------------------------------------------

CHAR_STATUSES = {"ok", "down", "stable", "dead"}


@dataclass
class Character:
    name: str
    archetype: str
    concept: str
    abilities: Dict[str, int]
    proficient_skills: List[str]
    inventory: List[str]
    hp: int
    max_hp: int
    ac: int
    personality: str = ""
    status: str = "ok"  # ok | down (dying) | stable (unconscious) | dead
    death_successes: int = 0
    death_failures: int = 0

    @property
    def alive(self) -> bool:
        return self.status != "dead"

    @property
    def can_act(self) -> bool:
        return self.status == "ok"

    def status_tag(self) -> str:
        return {
            "ok": "",
            "down": " [DYING]",
            "stable": " [UNCONSCIOUS]",
            "dead": " [DEAD]",
        }.get(self.status, "")

    def mod(self, ability: str) -> int:
        return ability_mod(self.abilities.get(ability.upper(), 10))

    def skill_bonus(self, skill: str) -> Tuple[int, str]:
        ability = SKILL_ABILITY.get(skill, "CHA")
        bonus = self.mod(ability)
        prof = ""
        if skill in self.proficient_skills:
            bonus += PROFICIENCY_BONUS
            prof = f" + prof {mod_str(PROFICIENCY_BONUS)}"
        return bonus, f"{ability} {mod_str(self.mod(ability))}{prof}"

    def attack_bonus(self, ability: str = "STR") -> int:
        return self.mod(ability) + PROFICIENCY_BONUS

    def sheet_text(self) -> str:
        abs_line = "  ".join(
            f"{a} {self.abilities[a]} ({mod_str(self.mod(a))})" for a in ABILITIES
        )
        skills = ", ".join(self.proficient_skills) or "(none)"
        items = ", ".join(self.inventory) or "(empty)"
        return (
            f"{self.name} — Level 1 {self.archetype}\n"
            f"  Concept: {self.concept}\n"
            f"  HP {self.hp}/{self.max_hp}  AC {self.ac}  Prof +{PROFICIENCY_BONUS}\n"
            f"  {abs_line}\n"
            f"  Skills: {skills}\n"
            f"  Inventory: {items}\n"
            f"  Notes: {self.personality or '—'}"
        )

    def compact(self) -> str:
        return (
            f"{self.name} ({self.archetype}) HP {self.hp}/{self.max_hp} AC {self.ac}"
            f"{self.status_tag()} | "
            f"Skills: {', '.join(self.proficient_skills)} | "
            f"Inv: {', '.join(self.inventory) if self.inventory else '(empty)'}"
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Character":
        abilities = {a: int(data.get("abilities", {}).get(a, 10)) for a in ABILITIES}
        hp = int(data.get("hp", 1))
        status = str(data.get("status") or ("ok" if hp > 0 else "down"))
        if status not in CHAR_STATUSES:
            status = "ok" if hp > 0 else "down"
        return cls(
            name=str(data.get("name", "Hero")),
            archetype=str(data.get("archetype", "Fighter")),
            concept=str(data.get("concept", "")),
            abilities=abilities,
            proficient_skills=list(data.get("proficient_skills") or []),
            inventory=list(data.get("inventory") or []),
            hp=hp,
            max_hp=int(data.get("max_hp", 1)),
            ac=int(data.get("ac", 10)),
            personality=str(data.get("personality", "")),
            status=status,
            death_successes=int(data.get("death_successes", 0)),
            death_failures=int(data.get("death_failures", 0)),
        )

    def to_prompt_block(self) -> str:
        abs_line = ", ".join(f"{a} {self.abilities[a]}" for a in ABILITIES)
        condition = {
            "ok": "healthy enough to act",
            "down": "at 0 HP, UNCONSCIOUS and DYING (rolling dying checks)",
            "stable": "at 0 HP, unconscious but stable (needs healing to wake)",
            "dead": "DEAD",
        }.get(self.status, "")
        return (
            f"- {self.name} | {self.archetype} | HP {self.hp}/{self.max_hp} | AC {self.ac}"
            f" | Condition: {condition}\n"
            f"  Abilities: {abs_line}\n"
            f"  Proficient: {', '.join(self.proficient_skills)}\n"
            f"  Inventory: {', '.join(self.inventory) if self.inventory else '(empty)'}\n"
            f"  Concept: {self.concept}"
        )


ADVENTURE_LENGTHS = {"short": 12, "standard": 24, "long": 40}
MAX_ENCOUNTER_FOES = 6


@dataclass
class Foe:
    """One enemy in a fight. Python owns HP — the LLM cannot invent endless packs."""
    id: str
    name: str
    hp: int
    max_hp: int
    ac: int = 12

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "ac": self.ac,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Foe":
        hp = max(0, int(data.get("hp", 10)))
        max_hp = max(1, int(data.get("max_hp", hp or 10)))
        return cls(
            id=str(data.get("id") or data.get("name") or "foe"),
            name=str(data.get("name") or "foe"),
            hp=hp,
            max_hp=max_hp,
            ac=max(8, min(20, int(data.get("ac", 12)))),
        )


@dataclass
class Encounter:
    title: str
    foes: List[Foe] = field(default_factory=list)
    started_round: int = 1

    def living(self) -> List[Foe]:
        return [f for f in self.foes if f.alive]

    def defeated_count(self) -> int:
        return sum(1 for f in self.foes if not f.alive)

    def summary_line(self) -> str:
        living = self.living()
        if not living:
            return f"{self.title}: CLEARED"
        bits = [f"{f.name} #{f.id.split('-')[-1]} HP {f.hp}/{f.max_hp} AC {f.ac}" for f in living]
        return (
            f"{self.title}: {len(living)} alive / {len(self.foes)} total "
            f"({'; '.join(bits)})"
        )

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "started_round": self.started_round,
            "foes": [f.to_dict() for f in self.foes],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Encounter":
        foes = [Foe.from_dict(f) for f in data.get("foes") or []]
        return cls(
            title=str(data.get("title") or "encounter"),
            foes=foes,
            started_round=int(data.get("started_round", 1)),
        )


@dataclass
class GameState:
    party: List[Character]
    location: str
    setting: str
    history: List[str] = field(default_factory=list)
    game_over: bool = False
    active_index: int = 0
    model: str = DEFAULT_MODEL
    round_num: int = 1
    campaign_length: int = 24
    acted_this_round: List[str] = field(default_factory=list)
    outcome: str = ""  # "" while playing, then "victory" or "defeat"
    # Multiplayer planning: hero name → action text, "__pass__", or "__death_save__"
    pending_actions: Dict[str, str] = field(default_factory=dict)
    encounter: Optional[Encounter] = None

    @property
    def active(self) -> Character:
        return self.party[self.active_index]

    @property
    def is_party_mode(self) -> bool:
        return len(self.party) > 1

    def find(self, name: str) -> Optional[Character]:
        key = name.strip().lower()
        for c in self.party:
            if c.name.lower() == key:
                return c
        # partial match
        for c in self.party:
            if key and key in c.name.lower():
                return c
        return None

    def heroes_who_must_plan(self) -> List[Character]:
        """Living heroes who still need to lock in an action this round."""
        return [
            c for c in self.party
            if c.status in ("ok", "down") and c.name not in self.pending_actions
        ]

    def banner(self) -> str:
        act_name, _ = act_for_round(self.round_num, self.campaign_length)
        lines = [
            f"Round {self.round_num}/{self.campaign_length} · {act_name}",
            f"Location: {self.location}",
            f"Active: {self.active.name}   ·   type help for commands",
            "Party:",
        ]
        for i, c in enumerate(self.party):
            mark = "*" if i == self.active_index else " "
            pending = self.pending_actions.get(c.name)
            extra = ""
            if pending == "__pass__":
                extra = "  (skipping)"
            elif pending == "__death_save__":
                extra = "  (dying check ready)"
            elif pending:
                extra = f"  (locked: {pending[:40]})"
            lines.append(f" {mark} {c.compact()}{extra}")
        if self.encounter and self.encounter.living():
            lines.append(f"Fight: {self.encounter.summary_line()}")
        waiting = self.heroes_who_must_plan()
        if self.is_party_mode and waiting:
            lines.append(
                "Planning: waiting for "
                + ", ".join(c.name for c in waiting)
            )
        return "\n".join(lines)

    def advance_turn(self) -> None:
        """Move to the next hero who can take a turn (acting or rolling dying checks)."""
        n = len(self.party)
        for step in range(1, n + 1):
            nxt = (self.active_index + step) % n
            if self.party[nxt].status in ("ok", "down"):
                self.active_index = nxt
                return

    def focus_next_planner(self) -> None:
        """Point active_index at the next hero who still owes a locked action."""
        waiting = self.heroes_who_must_plan()
        if waiting:
            self.active_index = self.party.index(waiting[0])
            return
        # Everyone planned — focus first living hero for after resolve
        for i, c in enumerate(self.party):
            if c.status in ("ok", "down"):
                self.active_index = i
                return

    def mark_acted(self, name: str) -> None:
        """Record that a hero took their turn; roll the round over when everyone has."""
        if name not in self.acted_this_round:
            self.acted_this_round.append(name)
        pending = [c.name for c in self.party if c.status in ("ok", "down")]
        if all(p in self.acted_this_round for p in pending):
            self.round_num += 1
            self.acted_this_round = []
            self.pending_actions = {}

    def finish_round(self) -> None:
        """Advance the campaign round after a full party resolve."""
        self.round_num += 1
        self.acted_this_round = []
        self.pending_actions = {}
        self.focus_next_planner()

    def to_dict(self) -> dict:
        return {
            "version": SAVE_VERSION,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "location": self.location,
            "setting": self.setting,
            "history": list(self.history),
            "game_over": self.game_over,
            "active_index": self.active_index,
            "model": self.model or DEFAULT_MODEL,
            "round_num": self.round_num,
            "campaign_length": self.campaign_length,
            "acted_this_round": list(self.acted_this_round),
            "outcome": self.outcome,
            "pending_actions": dict(self.pending_actions),
            "encounter": self.encounter.to_dict() if self.encounter else None,
            "party": [c.to_dict() for c in self.party],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GameState":
        party = [Character.from_dict(c) for c in data.get("party") or []]
        if not party:
            raise ValueError("Save has no party")
        idx = int(data.get("active_index", 0))
        idx = max(0, min(idx, len(party) - 1))
        pending = data.get("pending_actions") or {}
        if not isinstance(pending, dict):
            pending = {}
        enc_raw = data.get("encounter")
        encounter = Encounter.from_dict(enc_raw) if isinstance(enc_raw, dict) else None
        return cls(
            party=party,
            location=str(data.get("location", "unknown")),
            setting=str(data.get("setting", "")),
            history=list(data.get("history") or []),
            game_over=bool(data.get("game_over", False)),
            active_index=idx,
            model=str(data.get("model") or DEFAULT_MODEL),
            round_num=max(1, int(data.get("round_num", 1))),
            campaign_length=max(1, int(data.get("campaign_length", 24))),
            acted_this_round=list(data.get("acted_this_round") or []),
            outcome=str(data.get("outcome", "")),
            pending_actions={str(k): str(v) for k, v in pending.items()},
            encounter=encounter,
        )


# ---------------------------------------------------------------------------
# Saves
# ---------------------------------------------------------------------------

def _slot_path(slot: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", slot.strip()) or "quicksave"
    return SAVE_DIR / f"{safe}.json"


def list_saves() -> List[Path]:
    if not SAVE_DIR.exists():
        return []
    return sorted(SAVE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def save_game(state: GameState, slot: str = "quicksave") -> Path:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    path = _slot_path(slot)
    path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    return path


def load_game(slot: str) -> GameState:
    path = _slot_path(slot)
    if not path.exists():
        raise FileNotFoundError(slot)
    data = json.loads(path.read_text(encoding="utf-8"))
    return GameState.from_dict(data)


def print_save_list() -> None:
    saves = list_saves()
    if not saves:
        print("No saves yet. Use: save [slot]\n")
        return
    print("Save slots (newest first):")
    for path in saves:
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            when = meta.get("saved_at", "?")
            loc = meta.get("location", "?")
            names = ", ".join(c.get("name", "?") for c in meta.get("party") or [])
            print(f"  {path.stem:16}  {when}  @ {loc}  [{names}]")
        except (OSError, json.JSONDecodeError):
            print(f"  {path.stem:16}  (unreadable)")
    print(f"\nFolder: {SAVE_DIR}\n")


def print_help() -> None:
    print()
    print(HELP_TEXT)
    print()


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def _http_json(method: str, url: str, payload: Optional[dict] = None, timeout: float = 30) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_ollama_models() -> List[str]:
    """Return installed Ollama model names, or [] if Ollama is unreachable."""
    try:
        tags = _http_json("GET", f"{OLLAMA_HOST}/api/tags", timeout=3)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    names = []
    for m in tags.get("models") or []:
        name = str(m.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def model_choices(installed: Optional[List[str]] = None) -> List[Dict[str, str]]:
    """Build the model dropdown: recommended first, then any other installed models."""
    installed = installed if installed is not None else list_ollama_models()
    seen = set()
    choices: List[Dict[str, str]] = []
    # Prefer exact matches / prefix matches for recommended tags
    for mid, blurb in RECOMMENDED_MODELS.items():
        match = next((n for n in installed if n == mid or n.startswith(mid)), None)
        label_id = match or mid
        if label_id in seen:
            continue
        seen.add(label_id)
        mark = "" if match else " (not installed yet)"
        choices.append({"id": label_id, "label": f"{label_id} — {blurb}{mark}"})
    for name in installed:
        if name in seen:
            continue
        seen.add(name)
        choices.append({"id": name, "label": f"{name} — installed locally"})
    if not choices:
        for mid, blurb in RECOMMENDED_MODELS.items():
            choices.append({"id": mid, "label": f"{mid} — {blurb} (not installed yet)"})
    return choices


def pick_default_model(installed: Optional[List[str]] = None) -> str:
    """Prefer DEFAULT_MODEL when installed; otherwise first available model."""
    installed = installed if installed is not None else list_ollama_models()
    if not installed:
        return DEFAULT_MODEL
    if DEFAULT_MODEL in installed or any(DEFAULT_MODEL in n for n in installed):
        return next((n for n in installed if n == DEFAULT_MODEL or DEFAULT_MODEL in n), DEFAULT_MODEL)
    for mid in RECOMMENDED_MODELS:
        hit = next((n for n in installed if n == mid or mid in n), None)
        if hit:
            return hit
    return installed[0]


def check_ollama(model: str) -> None:
    try:
        tags = _http_json("GET", f"{OLLAMA_HOST}/api/tags", timeout=3)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        print(
            "\nCannot reach Ollama at http://localhost:11434\n"
            "Start it with: open -a Ollama   (macOS)  or  ollama serve\n"
            f"Then: ollama pull {DEFAULT_MODEL}\n"
        )
        sys.exit(1)

    names = [m.get("name", "") for m in tags.get("models", [])]
    if model not in names and not any(model in n for n in names):
        print(f"\nModel '{model}' not found. Installed: {names or '(none)'}\nRun: ollama pull {model}\n")
        sys.exit(1)


def call_ollama(prompt: str, model: str, temperature: float = 0.75, num_predict: int = 500) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "top_p": 0.9,
            "num_predict": num_predict,
        },
    }
    try:
        result = _http_json("POST", OLLAMA_URL, payload=payload, timeout=240)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return f"(Ollama error: {exc})"
    return str(result.get("response", "")).strip()


def extract_json_object(text: str) -> Optional[dict]:
    """Pull the first JSON object from a model reply."""
    text = text.strip()
    # fenced block
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fence:
        try:
            data = json.loads(fence.group(1))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
    # raw object (greedy from first { to last })
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def parse_status(raw: str) -> Tuple[str, Dict[str, Any]]:
    match = re.search(r"STATUS:\s*(\{.*\})\s*$", raw, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return raw.strip(), {}
    narrative = raw[: match.start()].strip()
    try:
        data = json.loads(match.group(1))
        return narrative, data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return narrative, {}


def print_wrapped(text: str) -> None:
    for para in text.split("\n"):
        if para.strip():
            print(textwrap.fill(para, width=88))
        else:
            print()


# ---------------------------------------------------------------------------
# Character creation (AI-assisted)
# ---------------------------------------------------------------------------

def normalize_character(data: dict, fallback_name: str) -> Character:
    archetype = str(data.get("archetype", "Fighter")).strip().title()
    if archetype not in ARCHETYPE_DEFAULTS:
        # fuzzy
        for key in ARCHETYPE_DEFAULTS:
            if key.lower() in archetype.lower() or archetype.lower() in key.lower():
                archetype = key
                break
        else:
            archetype = "Fighter"

    defaults = ARCHETYPE_DEFAULTS[archetype]
    name = str(data.get("name") or fallback_name).strip() or fallback_name
    concept = str(data.get("concept") or "a brave adventurer").strip()
    personality = str(data.get("personality") or "").strip()

    raw_abs = data.get("abilities") or {}
    abilities: Dict[str, int] = {}
    for a in ABILITIES:
        val = raw_abs.get(a, raw_abs.get(a.lower(), 12))
        try:
            abilities[a] = max(8, min(18, int(val)))
        except (TypeError, ValueError):
            abilities[a] = 12

    skills_in = data.get("proficient_skills") or defaults["skills"]
    skills: List[str] = []
    for s in skills_in:
        s = str(s).strip()
        # title-case match to known skills
        match = next((k for k in SKILL_ABILITY if k.lower() == s.lower()), None)
        if match and match not in skills:
            skills.append(match)
    if not skills:
        skills = list(defaults["skills"])

    inv_in = data.get("inventory") or defaults["gear"]
    inventory = [str(i).strip() for i in inv_in if str(i).strip()]

    con_mod = ability_mod(abilities["CON"])
    max_hp = defaults["hit_die"] + con_mod
    max_hp = max(1, max_hp)

    # Armor Class
    dex_mod = ability_mod(abilities["DEX"])
    if archetype == "Barbarian":
        ac = 10 + dex_mod + con_mod
    elif archetype in {"Rogue", "Ranger", "Wizard"}:
        ac = defaults["ac_base"] + dex_mod
    else:
        ac = defaults["ac_base"]

    # Allow AI override if sensible
    if isinstance(data.get("ac"), (int, float)):
        ac = max(10, min(20, int(data["ac"])))
    if isinstance(data.get("max_hp"), (int, float)):
        max_hp = max(1, int(data["max_hp"]))

    return Character(
        name=name,
        archetype=archetype,
        concept=concept,
        abilities=abilities,
        proficient_skills=skills,
        inventory=inventory,
        hp=max_hp,
        max_hp=max_hp,
        ac=ac,
        personality=personality,
    )


def create_character_with_ai(model: str, player_number: int, taken_names: List[str]) -> Character:
    print()
    print(f"—— Character {player_number} ——")
    name_hint = input("Name (blank = let the AI suggest): ").strip()
    print("In a sentence or two, what kind of hero is this?")
    print("(ex: smug half-elf thief who talks too much; stoic dwarf cleric haunted by a vision)")
    vibe = input("> ").strip() or "a bold young adventurer looking for trouble and treasure"
    print("Archetype hint? Fighter / Rogue / Wizard / Cleric / Ranger / Barbarian")
    arch_hint = input("[let AI choose]: ").strip()

    taken = ", ".join(taken_names) if taken_names else "(none)"
    prompt = f"""
You are helping create a level-1 fantasy adventurer for a teen-friendly dark fantasy game.

Player vibe: {vibe}
Name preference: {name_hint or "(invent a cool fitting name)"}
Archetype preference: {arch_hint or "(pick the best fit)"}
Names already taken (do not reuse): {taken}

Allowed archetypes: Fighter, Rogue, Wizard, Cleric, Ranger, Barbarian.

Assign ability scores using the standard array 15,14,13,12,10,8 (each number once),
arranged to fit the archetype and vibe.

Return ONLY valid JSON (no markdown) with this shape:
{{
  "name": "string",
  "archetype": "Fighter|Rogue|Wizard|Cleric|Ranger|Barbarian",
  "concept": "one short sentence",
  "personality": "one short sentence of voice/quirks",
  "abilities": {{"STR":0,"DEX":0,"CON":0,"INT":0,"WIS":0,"CHA":0}},
  "proficient_skills": ["Skill Name", "..."],
  "inventory": ["item", "..."]
}}

Use common fantasy skill names (Stealth, Persuasion, Athletics, etc.). Give 2–3 proficient skills that fit. Inventory should be iconic starter gear.
"""
    print("\nForging character with the local GM...\n")
    raw = call_ollama(prompt, model, temperature=0.7, num_predict=550)
    data = extract_json_object(raw) or {}
    character = normalize_character(data, fallback_name=name_hint or f"Hero{player_number}")

    # If player gave a name, prefer it
    if name_hint:
        character.name = name_hint

    print(character.sheet_text())
    print()
    ok = input("Keep this character? [Y/n]: ").strip().lower()
    if ok in {"n", "no"}:
        print("Tweaks (blank to skip):")
        new_name = input(f"  Name [{character.name}]: ").strip()
        new_arch = input(f"  Archetype [{character.archetype}]: ").strip()
        extra = input("  Extra note for the AI rewrite: ").strip()
        if new_name:
            name_hint = new_name
        if new_arch:
            arch_hint = new_arch
        vibe2 = f"{vibe}. Revision notes: {extra}" if extra else vibe
        prompt2 = prompt.replace(vibe, vibe2)
        if name_hint:
            prompt2 = prompt2.replace(name_hint or "(invent a cool fitting name)", name_hint)
        if arch_hint:
            prompt2 = prompt2.replace(arch_hint or "(pick the best fit)", arch_hint)
        raw = call_ollama(prompt2, model, temperature=0.65, num_predict=550)
        data = extract_json_object(raw) or data
        character = normalize_character(data, fallback_name=name_hint or character.name)
        if name_hint:
            character.name = name_hint
        print()
        print(character.sheet_text())
    return character


# ---------------------------------------------------------------------------
# Checks — AI proposes, Python resolves
# ---------------------------------------------------------------------------

DM_RULES = """\
You are the Game Master for a collaborative text RPG (teens ~13–15).
Tone: cinematic dark fantasy — peril and violence OK, no sexual content.

RULES AUTHORITY:
- Python owns all dice, HP, inventory, enemy HP, and pass/fail. You never invent roll results.
- Use a simple d20 system: d20 + ability mod (+2 proficiency if skilled) vs difficulty class.
- Typical difficulties: 10 easy, 13 medium, 15 hard, 18 very hard, 20 nearly impossible.
- Attack rolls vs target armor class. Damage only on hit.
- Be fair. Reward clever plans. Never decide the player's actions.

LIFE AND DEATH:
- A hero at 0 HP is unconscious and DYING. Python rolls their dying checks.
- Never kill a hero outright in narration and never narrate actions for a
  downed/unconscious hero — they are helpless until stabilized or healed.
- Allies can stabilize a dying hero (Medicine, DC 10) or heal them
  (potion ≈ 2d4+2, healing magic per its strength) — report healing as a
  positive hp_delta for that hero.
- Only true resurrection magic can bring back the DEAD, and it should be a
  rare quest reward.

ENCOUNTERS (CRITICAL — no endless fights):
- Fights use a FIXED roster tracked by Python. Respect the ENCOUNTER STATE block.
- Do NOT invent extra wolves/goblins/reinforcements mid-fight. Do NOT "another pack
  arrives" or "more emerge from the trees" once a fight has started.
- When ENCOUNTER STATE says CLEARED / no living foes, the fight is OVER — narrate
  the aftermath and move on. Do not spawn a new fight in the same beat.
- To begin a NEW fight only when none is active, end with STATUS including
  start_encounter listing every foe (max 6). Example foes entry:
  {"name":"wolf","hp":11,"ac":13}
- Prefer small packs (2–4). Boss fights may be one strong foe.
- end_encounter:true in STATUS ends the fight early (flee, surrender, trick, etc.).
"""


def act_for_round(round_num: int, campaign_length: int) -> Tuple[str, str]:
    """Return (act name, director's instruction) for campaign pacing."""
    frac = round_num / max(1, campaign_length)
    if frac <= 0.3:
        return (
            "Act I — The Hook",
            "Establish the world, introduce the central quest and its villain or "
            "mystery, and give the party a clear goal to chase.",
        )
    if frac <= 0.65:
        return (
            "Act II — Rising Danger",
            "Raise the stakes. Foreshadow the final villain/boss/puzzle. Throw "
            "meaningful complications at the party, but keep them moving toward "
            "the main goal.",
        )
    if frac <= 0.9:
        return (
            "Act III — The Climb",
            "Drive the party directly toward the final confrontation. Reveal the "
            "final boss, puzzle, or decisive choice. Do NOT introduce new side "
            "plots — converge everything.",
        )
    if frac <= 1.0:
        return (
            "FINALE",
            "This is the finale. Put the party face-to-face with the final boss, "
            "puzzle, or decisive confrontation RIGHT NOW. When it resolves, end "
            'the story: include "game_over": true and "victory": true or false '
            "in the STATUS line.",
        )
    return (
        "EPILOGUE (overdue)",
        "The story has run past its finale. Conclude THIS TURN: resolve the "
        'confrontation decisively and set "game_over": true with "victory" '
        "accordingly in the STATUS line.",
    )


def pacing_block(state: GameState) -> str:
    act_name, act_note = act_for_round(state.round_num, state.campaign_length)
    return (
        f"PACING: Round {state.round_num} of ~{state.campaign_length} — {act_name}.\n"
        f"DIRECTOR'S NOTE: {act_note}"
    )


def encounter_block(state: GameState) -> str:
    enc = state.encounter
    if not enc:
        return (
            "ENCOUNTER STATE: none active.\n"
            "If you start combat this beat, list EVERY foe up front via "
            "STATUS start_encounter (max 6). Do not drip-feed endless enemies."
        )
    living = enc.living()
    if not living:
        return (
            f"ENCOUNTER STATE: {enc.title} is CLEARED — all foes are down.\n"
            "Narrate the end of the fight and move on. Do NOT spawn replacements."
        )
    lines = [
        f"ENCOUNTER STATE: {enc.title} — FIXED roster "
        f"({len(living)} living / {len(enc.foes)} total). "
        "Do NOT add reinforcements.",
    ]
    for f in enc.foes:
        tag = "ALIVE" if f.alive else "DEFEATED"
        lines.append(f"  - [{tag}] {f.id}: {f.name} HP {f.hp}/{f.max_hp} AC {f.ac}")
    lines.append(
        "When attacking, target one ALIVE foe by name/id from this list only."
    )
    return "\n".join(lines)


def encounter_json(state: GameState) -> Optional[Dict[str, Any]]:
    enc = state.encounter
    if not enc or not enc.living():
        return None
    return {
        "title": enc.title,
        "living": len(enc.living()),
        "total": len(enc.foes),
        "foes": [
            {
                "id": f.id,
                "name": f.name,
                "hp": f.hp,
                "max_hp": f.max_hp,
                "ac": f.ac,
                "alive": f.alive,
            }
            for f in enc.foes
        ],
    }


def _slug_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "foe"
    return slug[:24]


def start_encounter(
    state: GameState,
    title: str,
    foe_specs: List[Dict[str, Any]],
) -> List[str]:
    """Begin a fixed-roster fight. Replaces nothing if a fight is already active."""
    events: List[str] = []
    if state.encounter and state.encounter.living():
        events.append(
            f"⚔ Fight already in progress ({state.encounter.title}) — "
            f"ignoring attempt to start another."
        )
        return events
    foes: List[Foe] = []
    counts: Dict[str, int] = {}
    for spec in foe_specs[:MAX_ENCOUNTER_FOES]:
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("name") or "foe").strip() or "foe"
        slug = _slug_name(name)
        counts[slug] = counts.get(slug, 0) + 1
        hp = max(1, min(80, int(spec.get("hp") or spec.get("max_hp") or 10)))
        foes.append(
            Foe(
                id=f"{slug}-{counts[slug]}",
                name=name,
                hp=hp,
                max_hp=hp,
                ac=max(8, min(20, int(spec.get("ac") or 12))),
            )
        )
    if not foes:
        return events
    state.encounter = Encounter(
        title=(title or "combat").strip() or "combat",
        foes=foes,
        started_round=state.round_num,
    )
    roster = ", ".join(f"{f.name} ({f.hp} HP)" for f in foes)
    events.append(
        f"⚔ Encounter started: {state.encounter.title} — {len(foes)} foe(s): {roster}. "
        f"This roster is fixed; no endless reinforcements."
    )
    return events


def end_encounter(state: GameState, reason: str = "") -> List[str]:
    if not state.encounter:
        return []
    title = state.encounter.title
    state.encounter = None
    note = f" ({reason})" if reason else ""
    return [f"⚔ Encounter over: {title}{note}"]


def find_foe(state: GameState, target: str) -> Optional[Foe]:
    if not state.encounter:
        return None
    key = target.strip().lower()
    living = state.encounter.living()
    if not living:
        return None
    # Exact id
    for f in living:
        if f.id.lower() == key:
            return f
    # Exact name
    exact = [f for f in living if f.name.lower() == key]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return min(exact, key=lambda f: f.hp)
    # Partial
    partial = [f for f in living if key in f.name.lower() or key in f.id.lower()]
    if partial:
        return min(partial, key=lambda f: f.hp)
    # Generic words like "closest wolf" / "wounded wolf"
    tokens = [t for t in re.split(r"[^a-z0-9]+", key) if t and t not in {
        "the", "a", "an", "closest", "nearest", "wounded", "injured", "last",
        "other", "that", "this", "with", "my",
    }]
    if tokens:
        scored = []
        for f in living:
            blob = f"{f.name} {f.id}".lower()
            if all(t in blob for t in tokens):
                scored.append(f)
            elif any(t in blob for t in tokens):
                scored.append(f)
        if scored:
            # Prefer wounded (lowest HP) when player said wounded/closest
            return min(scored, key=lambda f: f.hp)
    return living[0] if len(living) == 1 else None


def apply_attack_to_encounter(
    state: GameState,
    check_result: Optional[Dict[str, Any]],
) -> List[str]:
    """Apply Python-resolved attack damage to the fixed encounter roster."""
    events: List[str] = []
    if not check_result or check_result.get("check_type") != "attack":
        return events
    if not check_result.get("success"):
        return events
    dmg = int(check_result.get("damage") or 0)
    target = str(check_result.get("target") or "foe")
    ac = int(check_result.get("target_ac") or 12)
    hp_hint = int(check_result.get("target_hp") or 11)
    pack = int(check_result.get("encounter_size") or 1)

    if state.encounter is None or not state.encounter.living():
        # First successful attack against a foe can open a small fixed fight
        # if the DM forgot start_encounter.
        size = max(1, min(MAX_ENCOUNTER_FOES, pack if pack > 0 else 1))
        # Keep packs reasonable when auto-starting from a single attack
        if size > 4:
            size = 4
        specs = [{"name": target, "hp": hp_hint, "ac": ac} for _ in range(size)]
        events += start_encounter(state, f"fight vs {target}", specs)

    foe = find_foe(state, target)
    if not foe:
        events.append(
            f"⚠ No living foe matches '{target}'. "
            f"Living: {', '.join(f.id for f in (state.encounter.living() if state.encounter else [])) or 'none'}."
        )
        return events
    if dmg <= 0:
        return events

    foe.hp = max(0, foe.hp - dmg)
    events.append(f"🎯 {foe.name} ({foe.id}) takes {dmg} damage → HP {foe.hp}/{foe.max_hp}")
    if foe.hp <= 0:
        events.append(f"☠ {foe.name} ({foe.id}) is defeated!")
    if state.encounter and not state.encounter.living():
        events += end_encounter(state, "all foes defeated")
    return events


def apply_encounter_status(state: GameState, status: Dict[str, Any]) -> List[str]:
    """Handle start_encounter / end_encounter from STATUS. Blocks mid-fight adds."""
    events: List[str] = []
    if status.get("end_encounter") is True:
        events += end_encounter(state, "GM ended the fight")
    start = status.get("start_encounter")
    if isinstance(start, dict):
        if state.encounter and state.encounter.living():
            events.append(
                "⚔ Ignoring start_encounter — a fight is already active "
                "(no endless reinforcements)."
            )
        else:
            foes = start.get("foes") or start.get("enemies") or []
            if isinstance(foes, list):
                events += start_encounter(
                    state, str(start.get("title") or start.get("name") or "combat"), foes
                )
    # Explicitly reject reinforcement fields some models invent
    for banned in ("add_enemies", "reinforcements", "spawn", "more_enemies"):
        if banned in status:
            events.append(
                f"⚔ Ignoring '{banned}' — the encounter roster is fixed mid-fight."
            )
    return events


def party_block(state: GameState) -> str:
    return "\n".join(c.to_prompt_block() for c in state.party)


def propose_check(state: GameState, actor: Character, action: str, model: str) -> dict:
    history = "\n".join(state.history[-HISTORY_TURNS * 2 :]) or "(just starting)"
    prompt = f"""
{DM_RULES}

TASK: Decide if this action needs a die roll. Reply with ONLY JSON (no markdown).

WORLD: {state.setting}
LOCATION: {state.location}
PARTY:
{party_block(state)}

{encounter_block(state)}

ACTIVE CHARACTER: {actor.name}
PLAYER ACTION: {action}

RECENT STORY:
{history}

If NO meaningful risk/uncertainty (pure talk with no stakes, trivial movement, inspecting owned inventory), return:
{{"needs_check": false, "reason": "short reason"}}

If YES, return one of:

Skill/ability check:
{{
  "needs_check": true,
  "check_type": "skill",
  "character": "{actor.name}",
  "skill": "Stealth",
  "dc": 15,
  "advantage": false,
  "disadvantage": false,
  "reason": "what is at stake"
}}

Saving throw:
{{
  "needs_check": true,
  "check_type": "save",
  "character": "{actor.name}",
  "ability": "DEX",
  "dc": 13,
  "advantage": false,
  "disadvantage": false,
  "damage_on_fail": "1d6",
  "reason": "dodging falling rubble"
}}
(damage_on_fail: dice the character takes if they fail, when failure clearly
hurts them — traps, falls, poison, enemy attacks they must dodge. Use null if
failure costs something other than HP. Scale: 1d4 minor, 1d6 real, 2d6 serious.)

Attack:
{{
  "needs_check": true,
  "check_type": "attack",
  "character": "{actor.name}",
  "ability": "STR",
  "target": "wolf-1",
  "target_ac": 13,
  "target_hp": 11,
  "encounter_size": 3,
  "weapon": "longsword",
  "damage_die": "1d8",
  "advantage": false,
  "disadvantage": false,
  "reason": "striking the scout"
}}
(damage_die must be ONLY the weapon die like "1d8" or "2d6" — do NOT include
ability modifiers; Python adds those.)
(target MUST match an ALIVE foe id/name from ENCOUNTER STATE when a fight is
active. encounter_size + target_hp are ONLY used to open a NEW fight if none
is active — choose a small pack 1–4, never endless. Prefer existing foes.)

Use the active character's actual skills when choosing skill names.
"""
    raw = call_ollama(prompt, model, temperature=0.3, num_predict=280)
    data = extract_json_object(raw)
    if not data:
        return {"needs_check": False, "reason": "GM could not parse check; narrating freely"}
    return data


def resolve_check(actor: Character, proposal: dict) -> Tuple[str, Dict[str, Any]]:
    """Return (human-readable result lines, structured result for AI)."""
    ctype = str(proposal.get("check_type", "skill")).lower()
    adv = bool(proposal.get("advantage"))
    dis = bool(proposal.get("disadvantage"))
    reason = str(proposal.get("reason") or "the attempt")

    if ctype == "attack":
        ability = str(proposal.get("ability") or "STR").upper()
        if ability not in ABILITIES:
            ability = "STR"
        target = str(proposal.get("target") or "foe")
        target_ac = int(proposal.get("target_ac") or 12)
        weapon = str(proposal.get("weapon") or "weapon")
        damage_die = str(proposal.get("damage_die") or "1d6")
        bonus = actor.attack_bonus(ability)
        natural, d20_detail = d20_roll(adv, dis)
        total = natural + bonus
        crit = natural == 20
        fumble = natural == 1
        hit = crit or (not fumble and total >= target_ac)

        lines = [
            f"⚔ Attack — {actor.name} → {target} with {weapon}",
            f"   {d20_detail} {mod_str(bonus)} ({ability}+prof) = {total} vs AC {target_ac}",
        ]
        result: Dict[str, Any] = {
            "check_type": "attack",
            "character": actor.name,
            "target": target,
            "total": total,
            "natural": natural,
            "target_ac": target_ac,
            "success": hit,
            "critical": crit,
            "fumble": fumble,
            "reason": reason,
            "damage": 0,
            "damage_detail": "",
            "target_hp": max(1, int(proposal.get("target_hp") or 11)),
            "encounter_size": max(1, min(MAX_ENCOUNTER_FOES, int(proposal.get("encounter_size") or 1))),
        }
        if fumble:
            lines.append("   ✗ Critical miss!")
        elif hit:
            # damage — strip any bonus the LLM baked into damage_die; Python owns the mod
            damage_die = damage_die_only(damage_die)
            dmg_mod = actor.mod(ability)
            if crit:
                # roll damage dice twice on a critical hit + mod once
                a, _ = roll_dice(damage_die)
                b, _ = roll_dice(damage_die)
                dmg = a + b + dmg_mod
                detail = f"CRIT ({a}+{b}){mod_str(dmg_mod)}={dmg}"
            else:
                expr = f"{damage_die}{mod_str(dmg_mod)}" if dmg_mod else damage_die
                dmg, detail = roll_dice(expr)
            result["damage"] = max(0, dmg)
            result["damage_detail"] = detail
            lines.append(f"   ✓ HIT! Damage {detail}")
        else:
            lines.append("   ✗ Miss.")
        return "\n".join(lines), result

    if ctype == "save":
        ability = str(proposal.get("ability") or "DEX").upper()
        if ability not in ABILITIES:
            ability = "DEX"
        dc = int(proposal.get("dc") or 13)
        bonus = actor.mod(ability)
        natural, d20_detail = d20_roll(adv, dis)
        total = natural + bonus
        success = total >= dc
        lines = [
            f"🛡 Saving Throw — {actor.name} {ability} save",
            f"   {d20_detail} {mod_str(bonus)} = {total} vs DC {dc}  →  "
            f"{'SUCCESS' if success else 'FAILURE'} ({reason})",
        ]
        result = {
            "check_type": "save",
            "character": actor.name,
            "ability": ability,
            "dc": dc,
            "total": total,
            "natural": natural,
            "success": success,
            "reason": reason,
            "damage_to_self": 0,
        }
        dmg_expr = proposal.get("damage_on_fail")
        if not success and dmg_expr:
            dmg, detail = roll_dice(str(dmg_expr))
            if dmg > 0:
                result["damage_to_self"] = dmg
                lines.append(f"   💥 {actor.name} takes {detail} damage!")
        return "\n".join(lines), result

    # skill / ability check
    skill = str(proposal.get("skill") or "Athletics")
    match = next((k for k in SKILL_ABILITY if k.lower() == skill.lower()), None)
    if match:
        skill = match
        bonus, breakdown = actor.skill_bonus(skill)
        label = f"{skill} ({SKILL_ABILITY[skill]})"
    else:
        ability = str(proposal.get("ability") or "CHA").upper()
        if ability not in ABILITIES:
            ability = "CHA"
        bonus = actor.mod(ability)
        breakdown = f"{ability} {mod_str(bonus)}"
        label = f"{ability} check"

    dc = int(proposal.get("dc") or 13)
    natural, d20_detail = d20_roll(adv, dis)
    total = natural + bonus
    success = total >= dc
    lines = [
        f"🎲 Check — {actor.name}: {label}",
        f"   {d20_detail} + ({breakdown}) = {total} vs DC {dc}  →  "
        f"{'SUCCESS' if success else 'FAILURE'} ({reason})",
    ]
    result = {
        "check_type": "skill",
        "character": actor.name,
        "skill": skill if match else label,
        "dc": dc,
        "total": total,
        "natural": natural,
        "success": success,
        "reason": reason,
        "damage_to_self": 0,
    }
    dmg_expr = proposal.get("damage_on_fail")
    if not success and dmg_expr:
        dmg, detail = roll_dice(str(dmg_expr))
        if dmg > 0:
            result["damage_to_self"] = dmg
            lines.append(f"   💥 {actor.name} takes {detail} damage!")
    return "\n".join(lines), result


def narrate(
    state: GameState,
    actor: Character,
    action: Optional[str],
    model: str,
    check_result: Optional[Dict[str, Any]] = None,
    check_text: str = "",
) -> Tuple[str, Dict[str, Any]]:
    history = "\n".join(state.history[-HISTORY_TURNS * 2 :]) or "(adventure just starting)"
    if action is None:
        action_block = "Open the adventure with a vivid starting scene that hooks the whole party."
        roll_block = "No roll yet."
    else:
        action_block = f"ACTIVE CHARACTER: {actor.name}\nPLAYER ACTION: {action}"
        if check_result is None:
            roll_block = "No roll was needed."
        else:
            roll_block = (
                f"RESOLVED ROLL (authoritative — narrate consequences of this, do not re-roll):\n"
                f"{check_text}\nJSON: {json.dumps(check_result)}"
            )

    prompt = f"""
{DM_RULES}

WORLD: {state.setting}
LOCATION: {state.location}
PARTY:
{party_block(state)}

{pacing_block(state)}

{encounter_block(state)}

RECENT STORY:
{history}

{action_block}

{roll_block}

Write 2–4 short paragraphs of Game Master narration. End by prompting what the party does next.
Respect inventory, HP, and each hero's Condition. On a failed check, complications happen.
On success, progress with a cost sometimes. Follow the DIRECTOR'S NOTE for pacing.
Obey ENCOUNTER STATE — never invent endless reinforcements.

When HP/inventory/location change, end with exactly one STATUS line:

STATUS: {{"updates":[{{"name":"{actor.name}","hp_delta":0,"add_items":[],"remove_items":[]}}],"location":null,"game_over":false,"victory":false,"start_encounter":null,"end_encounter":false}}

- updates: per-character changes (hp_delta, add_items, remove_items)
- If your narration says a hero was hurt, you MUST report it as a negative
  hp_delta for that hero. Never describe damage without reporting it.
- If the resolved roll JSON already shows "damage_to_self" > 0, Python has
  already applied that damage — do NOT repeat it in hp_delta.
- Python already applied attack damage to foes — do not invent new enemies dying
  beyond what the rolls show.
- Healing is a positive hp_delta. Add "revive": true inside an update only
  for true resurrection of a DEAD hero.
- start_encounter: ONLY when no fight is active and combat begins. Example:
  {{"title":"wolf pack","foes":[{{"name":"wolf","hp":11,"ac":13}},{{"name":"wolf","hp":11,"ac":13}}]}}
  Max 6 foes. Prefer 2–4. null if not starting a fight.
- end_encounter: true to end a fight early (flee/surrender/trick). Python also
  ends it automatically when the last foe hits 0 HP.
- location: new location string or null
- game_over: true only when the story truly ends (final victory, or the
  heroes are beyond saving). Set "victory" true or false with it.
"""
    raw = call_ollama(prompt, model, temperature=0.85, num_predict=520)
    narrative, status = parse_status(raw)
    return narrative, status


def narrate_party_round(
    state: GameState,
    intents: List[Dict[str, Any]],
    model: str,
) -> Tuple[str, Dict[str, Any]]:
    """Narrate one shared round covering every hero's locked action."""
    history = "\n".join(state.history[-HISTORY_TURNS * 2 :]) or "(adventure just starting)"
    blocks: List[str] = []
    names: List[str] = []
    for intent in intents:
        names.append(str(intent.get("name", "?")))
        kind = intent.get("kind")
        if kind == "pass":
            blocks.append(f"- {intent['name']} skips this round (no action).")
        elif kind == "death_save":
            blocks.append(
                f"- {intent['name']} is dying and rolls a dying check:\n"
                f"  {intent.get('check_text', '')}"
            )
        else:
            action = intent.get("action", "")
            check_text = intent.get("check_text") or "No roll needed."
            check_json = intent.get("check_result")
            blocks.append(
                f"- {intent['name']}: {action}\n"
                f"  ROLL: {check_text}\n"
                f"  JSON: {json.dumps(check_json) if check_json else '{}'}"
            )
    action_block = "PARTY ROUND — resolve ALL of these together as simultaneous:\n" + "\n".join(blocks)
    example_name = names[0] if names else "Hero"
    prompt = f"""
{DM_RULES}

WORLD: {state.setting}
LOCATION: {state.location}
PARTY:
{party_block(state)}

{pacing_block(state)}

{encounter_block(state)}

RECENT STORY:
{history}

{action_block}

Write 3–5 short paragraphs of Game Master narration covering the WHOLE party's round.
Interweave their actions (they happen roughly at the same time). Include enemy/
world reactions once after their moves. End by prompting what the party does next.
Respect inventory, HP, and each hero's Condition. Follow the DIRECTOR'S NOTE.
Do not invent different roll results — the ROLL lines above are authoritative.
Skipped heroes take no action. Dying heroes are rolling dying checks, not acting.
Obey ENCOUNTER STATE — never invent endless reinforcements.

When HP/inventory/location change, end with exactly one STATUS line:

STATUS: {{"updates":[{{"name":"{example_name}","hp_delta":0,"add_items":[],"remove_items":[]}}],"location":null,"game_over":false,"victory":false,"start_encounter":null,"end_encounter":false}}

- updates: list changes for ANY affected heroes
- If narration hurts someone, report a negative hp_delta for them
- If a roll JSON already shows "damage_to_self" > 0, Python applied it — do not repeat
- Python already applied attack damage to foes — do not invent new enemies beyond rolls
- Healing is a positive hp_delta; "revive": true only for true resurrection of the DEAD
- start_encounter only when no fight is active (fixed foes, max 6); end_encounter to end early
- game_over + victory only when the story truly ends
"""
    raw = call_ollama(prompt, model, temperature=0.85, num_predict=700)
    narrative, status = parse_status(raw)
    return narrative, status


def apply_damage(char: Character, amount: int) -> List[str]:
    """Python-owned damage. Handles dropping to 0 HP and hits on dying heroes."""
    events: List[str] = []
    amount = max(0, int(amount))
    if amount == 0 or char.status == "dead":
        return events
    if char.status in ("down", "stable"):
        # Damage while dying counts as a dying-check failure.
        char.status = "down"
        char.death_failures += 1
        events.append(
            f"💔 {char.name} is struck while dying — dying-check failure "
            f"{char.death_failures}/3!"
        )
        if char.death_failures >= 3:
            char.status = "dead"
            events.append(f"💀 {char.name} has died.")
        return events
    char.hp = max(0, char.hp - amount)
    if char.hp == 0:
        char.status = "down"
        char.death_successes = 0
        char.death_failures = 0
        events.append(
            f"😵 {char.name} drops to 0 HP and is DYING! On their turns they roll "
            f"dying checks. An ally can stabilize them (Medicine DC 10) or heal them."
        )
    return events


def apply_healing(char: Character, amount: int, revive: bool = False) -> List[str]:
    """Python-owned healing. Wakes downed/stable heroes; the dead need revive magic."""
    events: List[str] = []
    amount = max(0, int(amount))
    if amount == 0:
        return events
    if char.status == "dead":
        if not revive:
            events.append(f"{char.name} is beyond ordinary healing.")
            return events
        char.status = "ok"
        char.death_successes = 0
        char.death_failures = 0
        char.hp = min(char.max_hp, max(1, amount))
        events.append(f"✨ {char.name} is restored to life with {char.hp} HP!")
        return events
    was_out = char.status in ("down", "stable")
    char.hp = min(char.max_hp, char.hp + amount)
    if was_out and char.hp > 0:
        char.status = "ok"
        char.death_successes = 0
        char.death_failures = 0
        events.append(f"💚 {char.name} is back on their feet with {char.hp} HP!")
    return events


def death_save(char: Character) -> Tuple[str, List[str]]:
    """Roll one death save for a dying hero. Returns (dice text, event lines)."""
    natural = roll_die(20)
    events: List[str] = []
    if natural == 20:
        char.status = "ok"
        char.hp = 1
        char.death_successes = 0
        char.death_failures = 0
        return (
            f"☠ Dying check — {char.name}: d20=20 → NATURAL 20! "
            f"{char.name} surges back to their feet with 1 HP!",
            events,
        )
    if natural == 1:
        char.death_failures += 2
        note = "CRITICAL FAILURE (counts as two)"
    elif natural >= 10:
        char.death_successes += 1
        note = "success"
    else:
        char.death_failures += 1
        note = "failure"
    text = (
        f"☠ Dying check — {char.name}: d20={natural} → {note} "
        f"(successes {char.death_successes}/3, failures {char.death_failures}/3)"
    )
    if char.death_failures >= 3:
        char.status = "dead"
        events.append(f"💀 {char.name} has died.")
    elif char.death_successes >= 3:
        char.status = "stable"
        events.append(
            f"💤 {char.name} is STABLE — unconscious at 0 HP until healed or the "
            f"party rests."
        )
    return text, events


def check_party_condition(state: GameState) -> List[str]:
    """Enforce party-wipe defeat and the 'everyone unconscious' mercy rule."""
    events: List[str] = []
    if state.game_over:
        return events
    if all(c.status == "dead" for c in state.party):
        state.game_over = True
        state.outcome = "defeat"
        events.append("💀 The whole party has fallen. The adventure ends here.")
        return events
    if not any(c.status in ("ok", "down") for c in state.party):
        # Everyone is stable-unconscious (or dead): fate intervenes at a cost.
        for c in state.party:
            if c.status == "stable":
                c.hp = 1
                c.status = "ok"
                c.death_successes = 0
                c.death_failures = 0
        events.append(
            "🌘 Darkness takes the party... but fate is not done with them. "
            "They awaken later at 1 HP — something has been lost."
        )
        state.history.append(
            "NOTE: The entire party was knocked unconscious. They awaken later at "
            "1 HP in a worse position — narrate the setback (captured, robbed of "
            "something precious, or displaced somewhere dangerous)."
        )
    return events


def apply_status(state: GameState, status: Dict[str, Any]) -> List[str]:
    """Apply the DM's STATUS updates. Returns human-readable event lines."""
    events: List[str] = []
    events += apply_encounter_status(state, status)

    for upd in status.get("updates") or []:
        if not isinstance(upd, dict):
            continue
        char = state.find(str(upd.get("name", "")))
        if not char:
            continue
        delta = upd.get("hp_delta", 0)
        if isinstance(delta, (int, float)) and int(delta):
            if int(delta) < 0:
                events += apply_damage(char, -int(delta))
            else:
                events += apply_healing(char, int(delta), revive=bool(upd.get("revive")))
        for item in upd.get("add_items") or []:
            if isinstance(item, str) and item and item not in char.inventory:
                char.inventory.append(item)
        for item in upd.get("remove_items") or []:
            if isinstance(item, str) and item in char.inventory:
                char.inventory.remove(item)

    # Back-compat single-character fields
    if "health_delta" in status and state.party:
        delta = status.get("health_delta", 0)
        if isinstance(delta, (int, float)) and int(delta):
            if int(delta) < 0:
                events += apply_damage(state.active, -int(delta))
            else:
                events += apply_healing(state.active, int(delta))

    loc = status.get("location")
    if isinstance(loc, str) and loc.strip():
        state.location = loc.strip()

    if status.get("game_over") is True:
        state.game_over = True
        if any(c.alive for c in state.party):
            state.outcome = "victory" if status.get("victory") is not False else "defeat"
        else:
            state.outcome = "defeat"
    events += check_party_condition(state)
    return events


def normalize_attack_proposal(state: GameState, proposal: dict) -> dict:
    """Pin attacks to the tracked encounter roster when a fight is active."""
    if not proposal.get("needs_check"):
        return proposal
    if str(proposal.get("check_type", "")).lower() != "attack":
        return proposal
    if not state.encounter or not state.encounter.living():
        # Cap pack size for opening blows
        try:
            size = int(proposal.get("encounter_size") or 1)
        except (TypeError, ValueError):
            size = 1
        proposal["encounter_size"] = max(1, min(4, size))
        return proposal
    target = str(proposal.get("target") or "")
    foe = find_foe(state, target)
    if foe:
        proposal["target"] = foe.id
        proposal["target_ac"] = foe.ac
        proposal["target_hp"] = foe.max_hp
    else:
        # Force a valid living target so the model can't invent "wolf 17"
        foe = min(state.encounter.living(), key=lambda f: f.hp)
        proposal["target"] = foe.id
        proposal["target_ac"] = foe.ac
        proposal["target_hp"] = foe.max_hp
    return proposal


# ---------------------------------------------------------------------------
# Turn engine (shared by CLI and web UI)
# ---------------------------------------------------------------------------

def auto_lock_downed(state: GameState) -> List[str]:
    """Dying heroes don't type actions — they auto-lock a death save for this round."""
    notes: List[str] = []
    for c in state.party:
        if c.status == "down" and c.name not in state.pending_actions:
            state.pending_actions[c.name] = "__death_save__"
            notes.append(f"{c.name} is dying and will roll a dying check this round.")
    return notes


def pending_summary(state: GameState) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for c in state.party:
        if c.status in ("dead", "stable"):
            rows.append({"name": c.name, "status": c.status, "plan": c.status})
            continue
        raw = state.pending_actions.get(c.name)
        if raw is None:
            plan = "waiting"
        elif raw == "__pass__":
            plan = "skipping"
        elif raw == "__death_save__":
            plan = "dying check"
        else:
            plan = raw
        rows.append({"name": c.name, "status": c.status, "plan": plan})
    return rows


def resolve_party_round(state: GameState, model: str) -> Dict[str, Any]:
    """Roll checks for every locked action, then narrate one shared party round."""
    intents: List[Dict[str, Any]] = []
    checks: List[str] = []
    events: List[str] = []

    # Stable order: party order
    for c in state.party:
        raw = state.pending_actions.get(c.name)
        if raw is None:
            continue
        if raw == "__pass__":
            intents.append({"name": c.name, "kind": "pass"})
            state.history.append(f"PLAYER ({c.name}): (skips this round)")
            continue
        if raw == "__death_save__" or c.status == "down":
            text, d_events = death_save(c)
            state.history.append(f"MECHANICS: {text}")
            events += d_events
            checks.append(text)
            intents.append({
                "name": c.name, "kind": "death_save",
                "check_text": text, "check_result": None,
            })
            continue

        action = raw
        state.history.append(f"PLAYER ({c.name}): {action}")
        proposal = normalize_attack_proposal(state, propose_check(state, c, action, model))
        check_text = ""
        check_result: Optional[Dict[str, Any]] = None
        if proposal.get("needs_check"):
            check_text, check_result = resolve_check(c, proposal)
            if check_result.get("damage_to_self"):
                events += apply_damage(c, int(check_result["damage_to_self"]))
            events += apply_attack_to_encounter(state, check_result)
            state.history.append(f"MECHANICS: {check_text}")
            checks.append(check_text)
        else:
            why = str(proposal.get("reason") or "no roll needed")
            check_text = f"No roll needed for {c.name} — {why}"
            checks.append(check_text)
        intents.append({
            "name": c.name,
            "kind": "action",
            "action": action,
            "check_text": check_text,
            "check_result": check_result,
            "reason": str(proposal.get("reason") or ""),
        })

    # If the whole party was only passing / death-saving with no living actions,
    # still narrate briefly so the world keeps moving — unless TPK.
    events += check_party_condition(state)
    narrative = ""
    if not state.game_over:
        narrative, status = narrate_party_round(state, intents, model)
        events += apply_status(state, status)
        state.history.append(f"DM: {narrative}")

    if not state.game_over:
        state.finish_round()

    return {
        "kind": "resolved",
        "message": "Everyone has locked in — resolving the party's round…",
        "checks": checks,
        "check": "\n\n".join(checks),
        "reason": "",
        "narrative": narrative,
        "events": events,
        "pending": pending_summary(state),
    }


def lock_party_choice(
    state: GameState,
    actor: Character,
    choice: str,
    model: str,
) -> Dict[str, Any]:
    """Lock one hero's plan. When the whole party is ready, resolve the round.

    choice: action text, or '__pass__'.
    """
    notes = auto_lock_downed(state)
    state.focus_next_planner()

    out: Dict[str, Any] = {
        "kind": "planned",
        "message": "",
        "check": "",
        "checks": [],
        "reason": "",
        "narrative": "",
        "events": [],
        "pending": [],
    }

    if actor.name in state.pending_actions:
        waiting = state.heroes_who_must_plan()
        state.focus_next_planner()
        who = ", ".join(c.name for c in waiting) if waiting else "(nobody)"
        out["kind"] = "blocked"
        out["message"] = (
            f"{actor.name} already locked in. Waiting for: {who}."
        )
        out["pending"] = pending_summary(state)
        return out

    if actor.status == "stable":
        out["kind"] = "blocked"
        out["message"] = (
            f"{actor.name} is unconscious (stable). Another hero must heal them."
        )
        out["pending"] = pending_summary(state)
        return out
    if actor.status == "dead":
        out["kind"] = "blocked"
        out["message"] = f"{actor.name} is dead and cannot act."
        out["pending"] = pending_summary(state)
        return out

    if actor.status == "down":
        state.pending_actions[actor.name] = "__death_save__"
        msg = f"{actor.name} is dying and will roll a dying check this round."
    elif choice == "__pass__":
        state.pending_actions[actor.name] = "__pass__"
        msg = f"{actor.name} skips this round."
    else:
        action = choice.strip()
        if not action:
            out["kind"] = "blocked"
            out["message"] = "Type an action before locking in."
            out["pending"] = pending_summary(state)
            return out
        state.pending_actions[actor.name] = action
        msg = f"{actor.name} locks in: {action}"

    waiting = state.heroes_who_must_plan()
    if waiting:
        state.focus_next_planner()
        msg += f" Waiting for: {', '.join(c.name for c in waiting)}."
        if notes:
            msg = " ".join(notes) + " " + msg
        out.update(message=msg, pending=pending_summary(state))
        return out

    # Everyone ready — resolve together
    resolved = resolve_party_round(state, model)
    if notes:
        resolved["message"] = " ".join(notes) + " " + resolved["message"]
    return resolved


def run_action_turn(state: GameState, actor: Character, action: str, model: str) -> Dict[str, Any]:
    """Submit an action. Solo resolves immediately; parties lock then resolve together."""
    if state.is_party_mode:
        auto_lock_downed(state)
        state.focus_next_planner()
        actor = state.active
        return lock_party_choice(state, actor, action, model)

    out: Dict[str, Any] = {
        "kind": "action", "message": "", "check": "", "checks": [],
        "reason": "", "narrative": "", "events": [], "pending": [],
    }
    if actor.status == "dead":
        out["kind"] = "blocked"
        out["message"] = f"{actor.name} is dead and cannot act. Choose a living hero."
        return out
    if actor.status == "stable":
        out["kind"] = "blocked"
        out["message"] = (
            f"{actor.name} is unconscious (stable). Another hero must heal or "
            f"wake them."
        )
        state.advance_turn()
        return out
    if actor.status == "down":
        text, events = death_save(actor)
        state.history.append(f"MECHANICS: {text}")
        for e in events:
            state.history.append(f"MECHANICS: {e}")
        state.mark_acted(actor.name)
        events += check_party_condition(state)
        if not state.game_over:
            state.advance_turn()
        out.update(
            kind="death_save",
            message=f"{actor.name} is dying and can only roll a dying check.",
            check=text,
            checks=[text],
            events=events,
        )
        return out

    state.history.append(f"PLAYER ({actor.name}): {action}")
    proposal = normalize_attack_proposal(state, propose_check(state, actor, action, model))
    check_text = ""
    check_result: Optional[Dict[str, Any]] = None
    events: List[str] = []
    if proposal.get("needs_check"):
        check_text, check_result = resolve_check(actor, proposal)
        if check_result.get("damage_to_self"):
            events += apply_damage(actor, int(check_result["damage_to_self"]))
        events += apply_attack_to_encounter(state, check_result)
        state.history.append(f"MECHANICS: {check_text}")

    narrative, status = narrate(
        state, actor, action, model, check_result=check_result, check_text=check_text
    )
    events += apply_status(state, status)
    state.history.append(f"DM: {narrative}")
    state.mark_acted(actor.name)
    if not state.game_over:
        state.advance_turn()
    out.update(
        check=check_text,
        checks=[check_text] if check_text else [],
        reason=str(proposal.get("reason") or "no roll needed"),
        narrative=narrative,
        events=events,
    )
    return out


def pass_turn(state: GameState, model: str = DEFAULT_MODEL) -> Dict[str, Any]:
    """Skip this hero's action for the round (or death-save if dying)."""
    if state.is_party_mode:
        notes = auto_lock_downed(state)
        state.focus_next_planner()
        actor = state.active
        result = lock_party_choice(state, actor, "__pass__", model)
        if notes and result.get("kind") == "planned":
            result["message"] = " ".join(notes) + " " + result["message"]
        return result

    actor = state.active
    out: Dict[str, Any] = {
        "kind": "pass", "check": "", "checks": [], "events": [],
        "message": "", "narrative": "", "reason": "", "pending": [],
    }
    if actor.status == "down":
        text, events = death_save(actor)
        state.history.append(f"MECHANICS: {text}")
        out["check"] = text
        out["checks"] = [text]
        out["events"] = events + check_party_condition(state)
        out["message"] = f"{actor.name} clings to life..."
    else:
        out["message"] = f"{actor.name} passes."
    state.mark_acted(actor.name)
    if not state.game_over:
        state.advance_turn()
        out["message"] += f" Active: {state.active.name}."
    return out


def ending_text(state: GameState) -> str:
    if state.outcome == "victory":
        return "🏆 VICTORY — the tale is complete! Start a new adventure to play again."
    if state.outcome == "defeat":
        return "💀 DEFEAT — the party has fallen. Start a new adventure to try again."
    return "— End of this tale —"


def ask_dm(
    state: GameState,
    question: str,
    model: str,
    asker: Optional[Character] = None,
) -> Dict[str, Any]:
    """Free clarifying question for the Game Master. Does not spend a turn or advance the round."""
    question = (question or "").strip()
    if not question:
        return {
            "kind": "blocked",
            "message": "Type a question for the GM first.",
            "narrative": "",
        }
    asker = asker or state.active
    history = "\n".join(state.history[-HISTORY_TURNS * 2 :]) or "(adventure just starting)"
    prompt = f"""
{DM_RULES}

WORLD: {state.setting}
LOCATION: {state.location}
PARTY:
{party_block(state)}

{pacing_block(state)}

{encounter_block(state)}

RECENT STORY:
{history}

A PLAYER IS ASKING YOU (THE GAME MASTER) A CLARIFYING QUESTION — this is NOT a game action.
Asker: {asker.name}
Question: {question}

RULES FOR YOUR ANSWER:
- Answer helpfully about what the party can already perceive, recall, or reasonably deduce.
- Respect ENCOUNTER STATE if a fight is active (how many foes remain, who looks wounded).
- Do NOT advance the plot, start a new scene, force an action, or decide what the players do.
- Do NOT invent unseen rooms/loot/enemies unless answering would spoil a secret they
  could not know — in that case say they can't tell from here.
- If the "question" is clearly an in-world action (attack, sneak, grab, cast…), briefly
  say they should submit that as an action (Do it / Lock in) instead of Ask GM.
- Keep it short: 1–2 tight paragraphs, conversational Game Master voice.
- Do NOT include a STATUS line. Do not change HP, inventory, or location.
"""
    answer = call_ollama(prompt, model, temperature=0.55, num_predict=280)
    # Strip accidental STATUS lines from chatty models
    answer, _ = parse_status(answer)
    if not answer.strip():
        answer = "(The Game Master has nothing further to add right now.)"
    state.history.append(f"ASK ({asker.name}): {question}")
    state.history.append(f"GM (aside): {answer}")
    return {
        "kind": "ask",
        "message": f"{asker.name} asks the GM (free — no turn spent).",
        "narrative": answer,
        "asker": asker.name,
        "question": question,
    }


# ---------------------------------------------------------------------------
# Setup & main loop
# ---------------------------------------------------------------------------

def choose_world() -> Tuple[str, str]:
    print("Pick a seed world (or type your own):")
    print("  1) Cursed forest & ruined keep (default)")
    print("  2) Neon cyberpunk city after the blackout")
    print("  3) Space freighter haunted by something in the cargo hold")
    print("  4) Custom")
    choice = input("Choice [1]: ").strip() or "1"
    presets = {
        "1": (
            "the Whispering Forest edge",
            "A dark-fantasy realm of cursed woods, bloodied relics, "
            "and a ruined keep that still whispers names.",
        ),
        "2": (
            "Sector 9 rooftops",
            "A rainy neon megacity after a city-wide blackout. Corpses of drones, "
            "black-market tech, and gangs that harvest memory chips from the dead.",
        ),
        "3": (
            "Freighter Erebus, Deck C",
            "A deep-space freighter weeks from port. The crew is thinning. "
            "Something in the cargo bay changes the emergency lights when it listens.",
        ),
    }
    if choice == "4":
        setting = input("Describe the world in 1–2 sentences:\n> ").strip()
        location = input("Starting location: ").strip() or "an unfamiliar threshold"
        if not setting:
            return presets["1"]
        return location, setting
    return presets.get(choice, presets["1"])


def setup_new_adventure(model: str) -> GameState:
    while True:
        raw = input("How many players? (1–4) [1]: ").strip() or "1"
        try:
            n = int(raw)
            if 1 <= n <= 4:
                break
        except ValueError:
            pass
        print("Enter 1, 2, 3, or 4.")

    party: List[Character] = []
    taken: List[str] = []
    for i in range(1, n + 1):
        c = create_character_with_ai(model, i, taken)
        party.append(c)
        taken.append(c.name)

    print()
    location, setting = choose_world()

    print("\nAdventure length (1 round = every hero takes a turn):")
    print("  1) Short    (~12 rounds)")
    print("  2) Standard (~24 rounds)  [default]")
    print("  3) Long     (~40 rounds)")
    pick = input("Choice [2]: ").strip() or "2"
    rounds = {"1": 12, "2": 24, "3": 40}.get(pick, 24)
    return GameState(
        party=party, location=location, setting=setting, campaign_length=rounds
    )


def startup(model: str) -> Tuple[GameState, bool]:
    """Return (state, needs_opening_scene)."""
    print("=" * 60)
    print("  THE INFINITE STORYTELLER")
    print("  Fantasy RPG · local GM · Python rolls the dice")
    print("=" * 60)
    print()
    print_help()

    saves = list_saves()
    if saves:
        print("1) New adventure")
        print("2) Continue from a save")
        choice = input("Choice [1]: ").strip() or "1"
        if choice == "2":
            print_save_list()
            slot = input("Load which slot? [quicksave]: ").strip() or "quicksave"
            try:
                state = load_game(slot)
            except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
                print(f"Could not load '{slot}': {exc}")
                print("Starting a new adventure instead.\n")
                return setup_new_adventure(model), True
            print(f"\nLoaded '{slot}'.")
            print(state.banner())
            print()
            return state, False

    state = setup_new_adventure(model)
    print()
    print("Type help anytime for the command list.")
    print("Dice rolls are always shown: d20 + mods vs DC/AC → SUCCESS/FAILURE.")
    print()
    return state, True


def parse_actor_prefix(state: GameState, action: str) -> Tuple[Character, str]:
    """Support 'Nyx: sneak past' style. Dead/unconscious heroes cannot be chosen."""
    if ":" in action:
        maybe_name, rest = action.split(":", 1)
        found = state.find(maybe_name.strip())
        if found and rest.strip() and found.status in ("ok", "down"):
            state.active_index = state.party.index(found)
            return found, rest.strip()
    return state.active, action


def free_roll(state: GameState, arg: str) -> None:
    arg = arg.strip()
    if not arg:
        arg = "d20"
    skill_match = next((k for k in SKILL_ABILITY if k.lower() == arg.lower()), None)
    if skill_match:
        actor = state.active
        bonus, breakdown = actor.skill_bonus(skill_match)
        natural, detail = d20_roll()
        total = natural + bonus
        print(
            f"🎲 Free roll — {actor.name}: {skill_match}\n"
            f"   {detail} + ({breakdown}) = {total}\n"
        )
        return
    if arg.upper() in ABILITIES:
        actor = state.active
        bonus = actor.mod(arg.upper())
        natural, detail = d20_roll()
        print(
            f"🎲 Free roll — {actor.name}: {arg.upper()}\n"
            f"   {detail} {mod_str(bonus)} = {natural + bonus}\n"
        )
        return
    total, detail = roll_dice(arg)
    print(f"🎲 Free roll: {arg} → {detail}\n")


def show_history(state: GameState) -> None:
    if not state.history:
        print("No story yet.\n")
        return
    print("── Recent story ──")
    for line in state.history[-12:]:
        print_wrapped(line)
        print()
    print("──────────────────\n")


def offer_quit_save(state: GameState) -> None:
    ans = input("Save before quitting? [Y/n]: ").strip().lower()
    if ans not in {"n", "no"}:
        slot = input("Save slot name [quicksave]: ").strip() or "quicksave"
        path = save_game(state, slot)
        print(f"Saved to {path}\n")
    print("The story folds itself shut. Until next time.\n")


def show_sheet(state: GameState, arg: str = "") -> None:
    if arg:
        c = state.find(arg)
        if c:
            print(c.sheet_text())
            print()
            return
        print(f"No character named '{arg}'.\n")
        return
    for c in state.party:
        print(c.sheet_text())
        print()


def take_story_turn(state: GameState, actor: Character, action: str, model: str) -> None:
    if state.is_party_mode:
        print("\nLocking in action...\n")
    else:
        print("\nConsulting the rules...\n")
    result = run_action_turn(state, actor, action, model)

    if result["kind"] == "blocked":
        print(result["message"] + "\n")
        return
    if result["kind"] == "planned":
        print(result["message"] + "\n")
        return
    if result["kind"] == "death_save":
        print(result["message"])
        print(result["check"])
        for e in result["events"]:
            print(e)
        print()
    else:
        if result.get("message"):
            print(result["message"] + "\n")
        if result["check"]:
            print(result["check"])
            print()
        elif result.get("reason"):
            print(f"No roll needed — {result['reason']}\n")
        if result.get("narrative"):
            print("...\n")
            print_wrapped(result["narrative"])
            print()
        for e in result["events"]:
            print(e)
        if result["events"]:
            print()

    if state.game_over:
        print(ending_text(state) + "\n")


def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
    check_ollama(model)
    state, needs_opening = startup(model)

    if needs_opening:
        print(state.banner())
        print("-" * 60)
        print("Summoning the Game Master...\n")
        narrative, status = narrate(state, state.active, None, model)
        apply_status(state, status)
        print_wrapped(narrative)
        state.history.append(f"DM: {narrative}")
        print()
    else:
        print("Welcome back. Type help for commands, or just describe an action.\n")

    while not state.game_over:
        print(state.banner())
        raw = input(f"\n[{state.active.name}] > ").strip()
        if not raw:
            continue

        lower = raw.lower()
        parts = raw.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if lower in {"help", "?", "commands"}:
            print_help()
            continue

        if lower in {"quit", "exit", "q"}:
            offer_quit_save(state)
            break

        if cmd == "save":
            slot = arg.strip() or "quicksave"
            path = save_game(state, slot)
            print(f"Saved game as '{Path(path).stem}' → {path}\n")
            continue

        if cmd == "load":
            slot = arg.strip() or "quicksave"
            try:
                state = load_game(slot)
                print(f"Loaded '{slot}'.\n")
                print(state.banner())
                print()
            except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
                print(f"Could not load '{slot}': {exc}")
                print("Try: saves\n")
            continue

        if lower in {"saves", "list"}:
            print_save_list()
            continue

        if lower in {"party", "status"}:
            print(state.banner() + "\n")
            continue

        if cmd in {"sheet", "sheets", "stats"}:
            show_sheet(state, arg)
            continue

        if lower in {"inventory", "i", "inv"}:
            for c in state.party:
                items = ", ".join(c.inventory) if c.inventory else "(empty)"
                print(f"{c.name}: {items}")
            print()
            continue

        if lower == "history":
            show_history(state)
            continue

        if cmd == "use":
            if not arg:
                print("Usage: use <character name>\n")
                continue
            found = state.find(arg)
            if not found:
                print(f"No character named '{arg}'.\n")
                continue
            if found.status not in ("ok", "down"):
                print(f"{found.name} is {found.status} and cannot take a turn.\n")
                continue
            state.active_index = state.party.index(found)
            print(f"Active hero is now {found.name}.\n")
            continue

        if lower in {"pass", "skip", "next"}:
            result = pass_turn(state, model)
            print(result["message"])
            if result.get("kind") == "planned":
                print()
                continue
            if result["check"]:
                print(result["check"])
            for e in result.get("events") or []:
                print(e)
            if result.get("narrative"):
                print()
                print_wrapped(result["narrative"])
            print()
            if state.game_over:
                print(ending_text(state) + "\n")
            continue

        if cmd == "roll":
            free_roll(state, arg or "d20")
            continue

        if cmd == "ask":
            if not arg.strip():
                print("Usage: ask <your question for the GM>\n")
                continue
            print("\nAsking the GM (free — no turn spent)...\n")
            result = ask_dm(state, arg.strip(), model)
            print(result["message"])
            print_wrapped(result["narrative"])
            print()
            continue

        if lower == "look":
            take_story_turn(
                state,
                state.active,
                "I take a careful look around and describe what I notice.",
                model,
            )
            continue

        actor, action = parse_actor_prefix(state, raw)
        if state.is_party_mode:
            # Hot-seat planning: always lock for the focused hero still waiting
            state.focus_next_planner()
            actor = state.active
        take_story_turn(state, actor, action, model)

    if state.game_over:
        print("Thanks for playing! Run python3 game.py to start a new tale.\n")


if __name__ == "__main__":
    main()
