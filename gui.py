#!/usr/bin/env python3
"""
Infinite Storyteller — macOS GUI front-end.

Setup uses modal dialogs (one at a time) so screens cannot overlap.
Terminal game.py is imported only — not modified.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, scrolledtext, simpledialog
from typing import Callable, List, Optional, Tuple

import game

BG = "#f4f1ea"
CARD = "#ffffff"
INK = "#111827"
MUTED = "#4b5563"
ACCENT = "#9a3412"
ACCENT_TXT = "#fff7ed"
STORY_BG = "#1c1917"
STORY_FG = "#f5f0e8"
FIELD_BG = "#ffffff"
FIELD_FG = "#111827"

WORLD_PRESETS = [
    (
        "Cursed forest & ruined keep",
        "the Whispering Forest edge",
        "A dark-fantasy realm of cursed woods, bloodied relics, "
        "and a ruined keep that still whispers names.",
    ),
    (
        "Neon cyberpunk city after the blackout",
        "Sector 9 rooftops",
        "A rainy neon megacity after a city-wide blackout. Corpses of drones, "
        "black-market tech, and gangs that harvest memory chips from the dead.",
    ),
    (
        "Space freighter haunted by cargo-bay terror",
        "Freighter Erebus, Deck C",
        "A deep-space freighter weeks from port. The crew is thinning. "
        "Something in the cargo bay changes the emergency lights when it listens.",
    ),
]

GUI_VERSION = "5"


def ensure_ollama_running() -> None:
    try:
        game._http_json("GET", f"{game.OLLAMA_HOST}/api/tags", timeout=2)
        return
    except Exception:
        subprocess.run(["open", "-a", "Ollama"], check=False)
        for _ in range(40):
            try:
                game._http_json("GET", f"{game.OLLAMA_HOST}/api/tags", timeout=1)
                return
            except Exception:
                time.sleep(0.5)


def forge_character(
    model: str,
    player_number: int,
    taken: List[str],
    name_hint: str,
    vibe: str,
    arch_hint: str,
) -> game.Character:
    taken_txt = ", ".join(taken) if taken else "(none)"
    prompt = f"""
You are helping create a Dungeons & Dragons level-1 character for a teen-friendly dark fantasy game.

Player vibe: {vibe}
Name preference: {name_hint or "(invent a cool fitting name)"}
Archetype preference: {arch_hint or "(pick the best fit)"}
Names already taken (do not reuse): {taken_txt}

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

Use real 5e skill names. Give 2–3 proficient skills that fit. Inventory should be iconic starter gear.
"""
    raw = game.call_ollama(prompt, model, temperature=0.7, num_predict=550)
    data = game.extract_json_object(raw) or {}
    character = game.normalize_character(
        data, fallback_name=name_hint or f"Hero{player_number}"
    )
    if name_hint:
        character.name = name_hint
    return character


class FormDialog(tk.Toplevel):
    """Modal cream dialog — keeps setup off the main window."""

    def __init__(self, master: tk.Tk, title: str, width: int = 560, height: int = 420):
        super().__init__(master)
        self.title(title)
        self.configure(bg=BG)
        self.resizable(True, True)
        self.geometry(f"{width}x{height}")
        self.transient(master)
        self.grab_set()
        self.result = None

        # Prefer light widgets even when macOS is in dark mode
        try:
            self.tk.call("tk::unsupported::MacWindowStyle", "appearance", self._w, "light")
        except tk.TclError:
            pass

        self.font_h = tkfont.Font(family="Helvetica", size=16, weight="bold")
        self.font_body = tkfont.Font(family="Helvetica", size=13)
        self.font_small = tkfont.Font(family="Helvetica", size=11)

        self.body = tk.Frame(self, bg=CARD, padx=20, pady=16)
        self.body.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.after(50, self._focus)

    def _focus(self) -> None:
        try:
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def heading(self, text: str) -> None:
        tk.Label(
            self.body, text=text, font=self.font_h, fg=ACCENT, bg=CARD, anchor="w"
        ).pack(anchor="w", pady=(0, 8))

    def note(self, text: str) -> None:
        tk.Label(
            self.body,
            text=text,
            font=self.font_body,
            fg=MUTED,
            bg=CARD,
            anchor="w",
            justify="left",
            wraplength=500,
        ).pack(anchor="w", pady=(0, 12))

    def label(self, text: str) -> None:
        tk.Label(
            self.body, text=text, font=self.font_small, fg=MUTED, bg=CARD, anchor="w"
        ).pack(anchor="w")

    def entry(self, var: tk.StringVar) -> tk.Entry:
        e = tk.Entry(
            self.body,
            textvariable=var,
            font=self.font_body,
            bg=FIELD_BG,
            fg=FIELD_FG,
            insertbackground=FIELD_FG,
            relief=tk.SOLID,
            borderwidth=2,
        )
        e.pack(fill=tk.X, pady=(2, 10), ipady=6)
        return e

    def buttons(self, ok_text: str, on_ok: Callable, cancel_text: str = "Cancel") -> None:
        row = tk.Frame(self.body, bg=CARD)
        row.pack(anchor="e", pady=(16, 0))
        tk.Button(
            row,
            text=cancel_text,
            command=self._cancel,
            font=self.font_body,
            bg="#e7e5e4",
            fg=INK,
            padx=12,
            pady=8,
        ).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(
            row,
            text=ok_text,
            command=on_ok,
            font=self.font_body,
            bg=ACCENT,
            fg=ACCENT_TXT,
            padx=14,
            pady=8,
        ).pack(side=tk.RIGHT)

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    def wait(self):
        self.wait_window(self)
        return self.result


class InfiniteStorytellerApp(tk.Tk):
    def __init__(self, model: str) -> None:
        super().__init__()
        self.model = model
        self.state: Optional[game.GameState] = None
        self.busy = False

        self.title(f"The Infinite Storyteller  (GUI v{GUI_VERSION})")
        self.geometry("980x720")
        self.minsize(840, 600)
        self.configure(bg=BG)
        try:
            self.tk.call("tk::unsupported::MacWindowStyle", "appearance", self._w, "light")
        except tk.TclError:
            pass

        self.font_title = tkfont.Font(family="Helvetica", size=26, weight="bold")
        self.font_body = tkfont.Font(family="Helvetica", size=14)
        self.font_small = tkfont.Font(family="Helvetica", size=12)
        self.font_mono = tkfont.Font(family="Menlo", size=12)

        self.status_var = tk.StringVar(value="Starting…")
        self.action_var = tk.StringVar()

        self._build_home()
        self.after(100, self._boot)

    def _build_home(self) -> None:
        # Wipe entire window body except we rebuild from scratch
        for w in self.winfo_children():
            w.destroy()

        wrap = tk.Frame(self, bg=BG, padx=36, pady=28)
        wrap.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            wrap,
            text="The Infinite Storyteller",
            font=self.font_title,
            fg=ACCENT,
            bg=BG,
        ).pack(anchor="w")
        tk.Label(
            wrap,
            text=f"Offline D&D adventure · local Ollama DM · GUI v{GUI_VERSION}",
            font=self.font_small,
            fg=MUTED,
            bg=BG,
        ).pack(anchor="w", pady=(4, 8))
        tk.Label(
            wrap,
            textvariable=self.status_var,
            font=self.font_mono,
            fg=MUTED,
            bg=BG,
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 18))

        card = tk.Frame(wrap, bg=CARD, padx=28, pady=24, highlightbackground="#a8a29e", highlightthickness=2)
        card.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            card,
            text="Welcome",
            font=tkfont.Font(family="Helvetica", size=18, weight="bold"),
            fg=INK,
            bg=CARD,
        ).pack(anchor="w")
        tk.Label(
            card,
            text=(
                "Play a text RPG with 1–4 heroes on this Mac.\n\n"
                "Click Start — a series of simple pop-up windows will ask for\n"
                "party size, hero details, and world. Then the story begins here."
            ),
            font=self.font_body,
            fg=MUTED,
            bg=CARD,
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(10, 20))

        row = tk.Frame(card, bg=CARD)
        row.pack(anchor="w")
        tk.Button(
            row,
            text="▶  Start a new adventure",
            command=self.start_new_adventure,
            font=self.font_body,
            bg=ACCENT,
            fg=ACCENT_TXT,
            activebackground="#7c2d12",
            padx=18,
            pady=12,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(
            row,
            text="Load a saved game",
            command=self.load_save,
            font=self.font_body,
            bg="#e7e5e4",
            fg=INK,
            padx=16,
            pady=12,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(
            row,
            text="How to play",
            command=self.howto,
            font=self.font_body,
            bg="#e7e5e4",
            fg=INK,
            padx=16,
            pady=12,
            cursor="hand2",
        ).pack(side=tk.LEFT)

        self.story = None
        self.entry = None
        self.go_btn = None

    def _build_play_screen(self) -> None:
        for w in self.winfo_children():
            w.destroy()

        wrap = tk.Frame(self, bg=BG, padx=16, pady=12)
        wrap.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            wrap,
            text=f"The Infinite Storyteller  (GUI v{GUI_VERSION})",
            font=tkfont.Font(family="Helvetica", size=16, weight="bold"),
            fg=ACCENT,
            bg=BG,
        ).pack(anchor="w")
        tk.Label(
            wrap,
            textvariable=self.status_var,
            font=self.font_mono,
            fg=MUTED,
            bg=BG,
            anchor="w",
            wraplength=920,
            justify="left",
        ).pack(fill=tk.X, pady=(2, 8))

        self.story = scrolledtext.ScrolledText(
            wrap,
            wrap=tk.WORD,
            font=("Georgia", 14),
            bg=STORY_BG,
            fg=STORY_FG,
            insertbackground=STORY_FG,
            relief=tk.FLAT,
            padx=14,
            pady=14,
            state=tk.DISABLED,
        )
        self.story.pack(fill=tk.BOTH, expand=True)
        self.story.tag_configure("dm", foreground=STORY_FG)
        self.story.tag_configure("player", foreground="#93c5fd")
        self.story.tag_configure("dice", foreground="#fbbf24")
        self.story.tag_configure("meta", foreground="#a8a29e", font=self.font_mono)

        tk.Label(
            wrap,
            text="Type what your hero does in the white box, then press Do it / Return.",
            font=self.font_small,
            fg=MUTED,
            bg=BG,
            anchor="w",
        ).pack(fill=tk.X, pady=(8, 4))

        btns = tk.Frame(wrap, bg=BG)
        btns.pack(fill=tk.X, pady=4)
        for text, cmd in [
            ("Help", self.on_help),
            ("Sheet", self.on_sheet),
            ("Inventory", self.on_inventory),
            ("Look", self.on_look),
            ("Pass turn", self.on_pass),
            ("Save", self.on_save),
            ("Menu", self.back_to_menu),
        ]:
            tk.Button(
                btns,
                text=text,
                command=cmd,
                font=self.font_small,
                bg="#e7e5e4",
                fg=INK,
                padx=10,
                pady=6,
            ).pack(side=tk.LEFT, padx=(0, 6))

        entry_row = tk.Frame(wrap, bg=BG)
        entry_row.pack(fill=tk.X, pady=(8, 0))
        self.entry = tk.Entry(
            entry_row,
            textvariable=self.action_var,
            font=self.font_body,
            bg=FIELD_BG,
            fg=FIELD_FG,
            insertbackground=FIELD_FG,
            relief=tk.SOLID,
            borderwidth=2,
        )
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8, padx=(0, 8))
        self.entry.bind("<Return>", lambda _e: self.on_submit())
        self.go_btn = tk.Button(
            entry_row,
            text="Do it",
            command=self.on_submit,
            font=self.font_body,
            bg=ACCENT,
            fg=ACCENT_TXT,
            padx=18,
            pady=8,
        )
        self.go_btn.pack(side=tk.RIGHT)

    # ----- boot / menu actions -----

    def _boot(self) -> None:
        self.status_var.set("Connecting to Ollama…")

        def work():
            ensure_ollama_running()
            game.check_ollama(self.model)
            return True

        def done(_):
            self.status_var.set(f"Ready · {self.model} · Click Start a new adventure")

        def err(exc: Exception):
            self.status_var.set(f"Ollama not ready: {exc}")
            messagebox.showwarning(
                "Ollama",
                f"Could not reach Ollama:\n{exc}\n\nOpen the Ollama app and try again.",
                parent=self,
            )

        self.run_bg(work, done, on_error=err)

    def howto(self) -> None:
        messagebox.showinfo(
            "How to play",
            "1) Start a new adventure\n"
            "2) Choose party size in the pop-up\n"
            "3) Create each hero (type a vibe, Forge)\n"
            "4) Pick a world\n"
            "5) Type actions in the story screen\n\n"
            "Dice rolls are shown automatically.",
            parent=self,
        )

    def start_new_adventure(self) -> None:
        # Step 1: party size
        n = self._ask_party_size()
        if n is None:
            return

        party: List[game.Character] = []
        taken: List[str] = []
        for i in range(1, n + 1):
            hero = self._ask_create_hero(i, n, taken)
            if hero is None:
                messagebox.showinfo("Cancelled", "Adventure setup cancelled.", parent=self)
                return
            party.append(hero)
            taken.append(hero.name)

        world = self._ask_world()
        if world is None:
            messagebox.showinfo("Cancelled", "Adventure setup cancelled.", parent=self)
            return
        location, setting = world

        self.state = game.GameState(party=party, location=location, setting=setting)
        self._enter_play(resume=False)

    def _ask_party_size(self) -> Optional[int]:
        dlg = FormDialog(self, "Step 1 — Party size", 480, 300)
        dlg.heading("How many players?")
        dlg.note("Turns are hot-seat on this laptop.")
        var = tk.StringVar(value="1")
        for val, label in [
            ("1", "1 player"),
            ("2", "2 players"),
            ("3", "3 players"),
            ("4", "4 players"),
        ]:
            tk.Radiobutton(
                dlg.body,
                text=label,
                variable=var,
                value=val,
                font=dlg.font_body,
                bg=CARD,
                fg=INK,
                selectcolor=FIELD_BG,
                activebackground=CARD,
                anchor="w",
            ).pack(anchor="w", pady=2)

        def ok() -> None:
            dlg.result = int(var.get())
            dlg.destroy()

        dlg.buttons("Next →", ok)
        return dlg.wait()

    def _ask_create_hero(
        self, num: int, total: int, taken: List[str]
    ) -> Optional[game.Character]:
        while True:
            dlg = FormDialog(self, f"Step 2 — Hero {num} of {total}", 640, 620)
            dlg.heading(f"Create hero {num} of {total}")
            dlg.note("Type in the white boxes. Name can be blank — the AI will invent one.")

            name_var = tk.StringVar()
            vibe_var = tk.StringVar(
                value="a bold young adventurer looking for trouble and treasure"
            )
            arch_var = tk.StringVar(value="Let AI choose")

            dlg.label("NAME (optional)")
            name_entry = dlg.entry(name_var)
            dlg.label("HERO VIBE / DESCRIPTION")
            dlg.entry(vibe_var)
            dlg.label("ARCHETYPE (click one)")

            # Radiobuttons — OptionMenu menus are unreadable in macOS dark mode
            arch_box = tk.Frame(dlg.body, bg="#f5f5f4", highlightbackground="#a8a29e",
                                highlightthickness=2, padx=10, pady=8)
            arch_box.pack(fill=tk.X, pady=(2, 10))
            for label in [
                "Let AI choose",
                "Fighter",
                "Rogue",
                "Wizard",
                "Cleric",
                "Ranger",
                "Barbarian",
            ]:
                tk.Radiobutton(
                    arch_box,
                    text=label,
                    variable=arch_var,
                    value=label,
                    font=dlg.font_body,
                    bg="#f5f5f4",
                    fg="#111827",
                    activebackground="#e7e5e4",
                    activeforeground="#111827",
                    selectcolor="#ffffff",
                    anchor="w",
                    highlightthickness=0,
                ).pack(anchor="w", pady=1)

            status = tk.Label(dlg.body, text="", font=dlg.font_small, fg=MUTED, bg=CARD, anchor="w")
            status.pack(anchor="w")

            forged: dict = {}

            def do_forge() -> None:
                status.configure(text="Forging with AI… please wait (~10–20 sec)")
                dlg.update_idletasks()
                arch = arch_var.get()
                if arch == "Let AI choose":
                    arch = ""
                try:
                    character = forge_character(
                        self.model,
                        num,
                        taken,
                        name_var.get().strip(),
                        vibe_var.get().strip() or "a bold adventurer",
                        arch,
                    )
                except Exception as exc:  # noqa: BLE001
                    status.configure(text=f"Failed: {exc}")
                    return
                forged["c"] = character
                dlg.result = "forged"
                dlg.destroy()

            dlg.buttons("Forge with AI", do_forge)
            name_entry.focus_set()
            outcome = dlg.wait()
            if outcome is None:
                return None

            character = forged.get("c")
            if not character:
                return None

            keep = messagebox.askyesno(
                "Keep this hero?",
                f"{character.sheet_text()}\n\nKeep this character?",
                parent=self,
            )
            if keep:
                return character
            # loop to try again

    def _ask_world(self) -> Optional[Tuple[str, str]]:
        dlg = FormDialog(self, "Step 3 — Choose a world", 640, 520)
        dlg.heading("Choose a world")
        dlg.note("Pick a preset, or choose Custom and fill the boxes.")
        var = tk.StringVar(value="0")
        for i, (title, _loc, _setting) in enumerate(WORLD_PRESETS):
            tk.Radiobutton(
                dlg.body,
                text=title,
                variable=var,
                value=str(i),
                font=dlg.font_body,
                bg=CARD,
                fg=INK,
                selectcolor=FIELD_BG,
                activebackground=CARD,
                anchor="w",
                wraplength=540,
                justify="left",
            ).pack(anchor="w", pady=2)
        tk.Radiobutton(
            dlg.body,
            text="Custom world",
            variable=var,
            value="custom",
            font=dlg.font_body,
            bg=CARD,
            fg=INK,
            selectcolor=FIELD_BG,
            activebackground=CARD,
            anchor="w",
        ).pack(anchor="w", pady=2)

        dlg.label("CUSTOM DESCRIPTION")
        custom = tk.Text(
            dlg.body,
            height=3,
            font=dlg.font_body,
            bg=FIELD_BG,
            fg=FIELD_FG,
            relief=tk.SOLID,
            borderwidth=2,
            wrap=tk.WORD,
        )
        custom.pack(fill=tk.X, pady=(2, 8))
        loc_var = tk.StringVar(value="an unfamiliar threshold")
        dlg.label("CUSTOM STARTING LOCATION")
        dlg.entry(loc_var)

        def ok() -> None:
            choice = var.get()
            if choice == "custom":
                setting = custom.get("1.0", tk.END).strip()
                if not setting:
                    messagebox.showinfo("Custom", "Please describe the custom world.", parent=dlg)
                    return
                dlg.result = (loc_var.get().strip() or "an unfamiliar threshold", setting)
            else:
                _t, location, setting = WORLD_PRESETS[int(choice)]
                dlg.result = (location, setting)
            dlg.destroy()

        dlg.buttons("Begin adventure →", ok)
        return dlg.wait()

    def load_save(self) -> None:
        saves = game.list_saves()
        if not saves:
            messagebox.showinfo(
                "No saves",
                "No saved games yet.\nStart a new adventure first.",
                parent=self,
            )
            return
        names = [p.stem for p in saves]
        # simple choice
        pick = simpledialog.askstring(
            "Load save",
            "Type a save slot name:\n\n" + "\n".join(f"• {n}" for n in names),
            initialvalue=names[0],
            parent=self,
        )
        if not pick:
            return
        try:
            self.state = game.load_game(pick.strip())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Load failed", str(exc), parent=self)
            return
        self._enter_play(resume=True)

    def back_to_menu(self) -> None:
        if self.state and messagebox.askyesno(
            "Leave adventure", "Save before returning to menu?", parent=self
        ):
            self.on_save()
        self.state = None
        self._build_home()
        self.status_var.set(f"Ready · {self.model}")

    def _enter_play(self, resume: bool) -> None:
        self._build_play_screen()
        self.refresh_status()
        if resume:
            self.append("Welcome back — save loaded.", "meta")
            if self.state and self.state.history:
                last = self.state.history[-1]
                if last.startswith("DM: "):
                    self.append(last[4:], "dm")
            if self.entry:
                self.entry.focus_set()
            return

        self.append("Summoning the Dungeon Master…", "meta")
        self.set_busy(True, "Writing opening scene…")

        def work():
            return game.narrate(self.state, self.state.active, None, self.model)

        def done(result):
            narrative, status = result
            game.apply_status(self.state, status)
            assert self.state is not None
            self.state.history.append(f"DM: {narrative}")
            self.append(narrative, "dm")
            self.append('Your turn — try: "look around" or "sneak forward".', "meta")
            self.set_busy(False)
            if self.entry:
                self.entry.focus_set()

        self.run_bg(work, done)

    # ----- helpers -----

    def append(self, text: str, tag: str = "dm") -> None:
        if not self.story:
            return
        self.story.configure(state=tk.NORMAL)
        self.story.insert(tk.END, text.rstrip() + "\n\n", tag)
        self.story.see(tk.END)
        self.story.configure(state=tk.DISABLED)

    def refresh_status(self) -> None:
        if not self.state:
            self.status_var.set(f"Ready · {self.model}")
            return
        self.status_var.set(self.state.banner().replace("\n", "  ·  "))

    def set_busy(self, busy: bool, note: str = "") -> None:
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        if self.entry:
            self.entry.configure(state=state)
        if self.go_btn:
            self.go_btn.configure(state=state)
        if note:
            self.status_var.set(note)
        elif not busy:
            self.refresh_status()

    def run_bg(
        self,
        work: Callable,
        on_done: Callable,
        on_error: Optional[Callable] = None,
    ) -> None:
        def wrapper() -> None:
            err = None
            result = None
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                err = exc

            def finish() -> None:
                if err is not None:
                    if on_error:
                        on_error(err)
                    else:
                        messagebox.showerror("Error", str(err), parent=self)
                        self.set_busy(False)
                    return
                on_done(result)

            self.after(0, finish)

        threading.Thread(target=wrapper, daemon=True).start()

    def on_help(self) -> None:
        messagebox.showinfo("Help", game.HELP_TEXT, parent=self)

    def on_sheet(self) -> None:
        if not self.state:
            return
        messagebox.showinfo(
            "Character sheets",
            "\n\n".join(c.sheet_text() for c in self.state.party),
            parent=self,
        )

    def on_inventory(self) -> None:
        if not self.state:
            return
        lines = [
            f"{c.name}: {', '.join(c.inventory) if c.inventory else '(empty)'}"
            for c in self.state.party
        ]
        messagebox.showinfo("Inventory", "\n".join(lines), parent=self)

    def on_look(self) -> None:
        if self.busy or not self.state:
            return
        self._do_action("I take a careful look around and describe what I notice.")

    def on_pass(self) -> None:
        if not self.state or self.busy:
            return
        prev = self.state.active.name
        self.state.advance_turn()
        self.append(f"{prev} passes. Active: {self.state.active.name}.", "meta")
        self.refresh_status()

    def on_save(self) -> None:
        if not self.state:
            return
        slot = simpledialog.askstring(
            "Save", "Save slot name:", initialvalue="quicksave", parent=self
        )
        if not slot:
            return
        path = game.save_game(self.state, slot.strip())
        self.append(f"Saved as '{path.stem}'", "meta")
        messagebox.showinfo("Saved", f"Saved as {path.stem}", parent=self)

    def on_submit(self) -> None:
        if self.busy or not self.state:
            return
        raw = self.action_var.get().strip()
        if not raw:
            return
        self.action_var.set("")
        lower = raw.lower()
        if lower in {"help", "?"}:
            self.on_help()
            return
        if lower in {"sheet", "stats"}:
            self.on_sheet()
            return
        if lower in {"inventory", "i"}:
            self.on_inventory()
            return
        if lower == "look":
            self.on_look()
            return
        if lower in {"pass", "skip", "next"}:
            self.on_pass()
            return
        self._do_action(raw)

    def _do_action(self, raw: str) -> None:
        assert self.state is not None
        actor, action = game.parse_actor_prefix(self.state, raw)
        self.append(f"[{actor.name}] {action}", "player")
        self.set_busy(True, "Consulting the rules / rolling dice…")

        def work():
            proposal = game.propose_check(self.state, actor, action, self.model)
            check_text = ""
            check_result = None
            if proposal.get("needs_check"):
                check_text, check_result = game.resolve_check(actor, proposal)
            narrative, status = game.narrate(
                self.state,
                actor,
                action,
                self.model,
                check_result=check_result,
                check_text=check_text,
            )
            return proposal, check_text, narrative, status

        def done(result):
            proposal, check_text, narrative, status = result
            assert self.state is not None
            self.state.history.append(f"PLAYER ({actor.name}): {action}")
            if proposal.get("needs_check") and check_text:
                self.append(check_text, "dice")
            else:
                self.append(
                    f"No roll needed — {proposal.get('reason') or 'no roll needed'}",
                    "meta",
                )
            game.apply_status(self.state, status)
            self.state.history.append(f"DM: {narrative}")
            self.append(narrative, "dm")
            self.state.advance_turn()
            self.set_busy(False)
            if all(c.hp <= 0 for c in self.state.party):
                self.state.game_over = True
                self.append("The whole party has fallen.", "meta")
            elif status.get("game_over"):
                self.state.game_over = True
                self.append("— End of this tale —", "meta")
            if self.entry:
                self.entry.focus_set()

        self.run_bg(work, done)


def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else game.DEFAULT_MODEL
    app = InfiniteStorytellerApp(model)
    app.mainloop()


if __name__ == "__main__":
    main()
