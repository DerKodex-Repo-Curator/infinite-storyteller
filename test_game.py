#!/usr/bin/env python3
"""Offline smoke tests for The Infinite Storyteller.

No Ollama required — the LLM is stubbed. Run with:
    python3 test_game.py
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

import game
import gui_web


def make_char(name: str = "Hero", hp: int = 10) -> game.Character:
    return game.Character(
        name=name,
        archetype="Fighter",
        concept="test hero",
        abilities={a: 12 for a in game.ABILITIES},
        proficient_skills=["Athletics"],
        inventory=["sword"],
        hp=hp,
        max_hp=10,
        ac=14,
    )


def make_state(n: int = 1, **kw) -> game.GameState:
    return game.GameState(
        party=[make_char(f"H{i + 1}") for i in range(n)],
        location="test cave",
        setting="test world",
        **kw,
    )


class RollQueue:
    """Deterministic stand-in for game.roll_die."""

    def __init__(self, values):
        self.values = list(values)

    def __call__(self, sides: int) -> int:
        return self.values.pop(0) if self.values else 10


class EncounterTracking(unittest.TestCase):
    def test_start_and_kill_clears_fight(self):
        s = make_state(1)
        events = game.start_encounter(s, "wolf pack", [
            {"name": "wolf", "hp": 5, "ac": 13},
            {"name": "wolf", "hp": 5, "ac": 13},
        ])
        self.assertTrue(s.encounter)
        self.assertEqual(len(s.encounter.living()), 2)
        self.assertTrue(any("fixed" in e.lower() or "Encounter started" in e for e in events))

        ev = game.apply_attack_to_encounter(s, {
            "check_type": "attack", "success": True, "damage": 5,
            "target": "wolf-1", "target_ac": 13, "target_hp": 5, "encounter_size": 2,
        })
        self.assertEqual(len(s.encounter.living()), 1)
        self.assertTrue(any("defeated" in e for e in ev))

        ev = game.apply_attack_to_encounter(s, {
            "check_type": "attack", "success": True, "damage": 9,
            "target": "wounded wolf", "target_ac": 13,
        })
        self.assertIsNone(s.encounter)
        self.assertTrue(any("Encounter over" in e for e in ev))

    def test_mid_fight_start_ignored(self):
        s = make_state(1)
        game.start_encounter(s, "goblins", [{"name": "goblin", "hp": 7, "ac": 12}] * 2)
        events = game.apply_status(s, {
            "start_encounter": {
                "title": "endless wolves",
                "foes": [{"name": "wolf", "hp": 11, "ac": 13}] * 6,
            }
        })
        self.assertEqual(s.encounter.title, "goblins")
        self.assertEqual(len(s.encounter.foes), 2)
        self.assertTrue(any("Ignoring start_encounter" in e for e in events))

    def test_reinforcements_field_ignored(self):
        s = make_state(1)
        game.start_encounter(s, "pack", [{"name": "wolf", "hp": 11, "ac": 13}])
        events = game.apply_status(s, {"reinforcements": [{"name": "wolf"}]})
        self.assertEqual(len(s.encounter.foes), 1)
        self.assertTrue(any("Ignoring 'reinforcements'" in e for e in events))

    def test_normalize_attack_pins_to_roster(self):
        s = make_state(1)
        game.start_encounter(s, "pack", [
            {"name": "wolf", "hp": 11, "ac": 13},
            {"name": "wolf", "hp": 3, "ac": 13},
        ])
        prop = game.normalize_attack_proposal(s, {
            "needs_check": True,
            "check_type": "attack",
            "target": "another fresh wolf from the trees",
            "target_ac": 99,
        })
        self.assertIn(prop["target"], {"wolf-1", "wolf-2"})
        self.assertEqual(prop["target_ac"], 13)

    def test_auto_start_from_first_hit_capped(self):
        s = make_state(1)
        game.apply_attack_to_encounter(s, {
            "check_type": "attack", "success": True, "damage": 4,
            "target": "bandit", "target_ac": 12, "target_hp": 10,
            "encounter_size": 8,  # should cap
        })
        self.assertIsNotNone(s.encounter)
        self.assertLessEqual(len(s.encounter.foes), 4)
        self.assertEqual(s.encounter.living()[0].hp, 6)  # 10-4 on matched foe


class DiceParsing(unittest.TestCase):
    def test_multiple_trailing_bonuses(self):
        original = game.roll_die
        try:
            game.roll_die = RollQueue([5])
            total, detail = game.roll_dice("1d8+2+2")
            self.assertEqual(total, 9)
            self.assertIn("=9", detail)
            self.assertNotIn("invalid", detail)
        finally:
            game.roll_die = original

    def test_damage_die_only_strips_llm_bonus(self):
        self.assertEqual(game.damage_die_only("1d8+2"), "1d8")
        self.assertEqual(game.damage_die_only("2d6+3+1"), "2d6")
        self.assertEqual(game.damage_die_only("d10"), "1d10")

    def test_attack_damage_does_not_double_mod(self):
        original = game.roll_die
        try:
            game.roll_die = RollQueue([14, 5])  # to-hit d20, then damage die
            actor = make_char("Berf")
            actor.abilities["STR"] = 14  # +2
            text, result = game.resolve_check(actor, {
                "check_type": "attack",
                "ability": "STR",
                "target": "wolf",
                "target_ac": 10,
                "weapon": "battleaxe",
                "damage_die": "1d8+2",  # LLM mistakenly included mod
            })
            self.assertTrue(result["success"])
            self.assertEqual(result["damage"], 7)  # 5 + STR(+2), not 5+2+2
            self.assertNotIn("invalid", text)
        finally:
            game.roll_die = original


class DeathAndRevival(unittest.TestCase):
    def test_damage_downs_hero(self):
        c = make_char()
        events = game.apply_damage(c, 12)
        self.assertEqual(c.hp, 0)
        self.assertEqual(c.status, "down")
        self.assertTrue(any("DYING" in e for e in events))

    def test_damage_while_dying_adds_failures_then_death(self):
        c = make_char(hp=0)
        c.status = "down"
        game.apply_damage(c, 3)
        game.apply_damage(c, 3)
        events = game.apply_damage(c, 3)
        self.assertEqual(c.status, "dead")
        self.assertTrue(any("died" in e for e in events))

    def test_healing_wakes_downed_hero(self):
        c = make_char(hp=0)
        c.status = "down"
        c.death_failures = 2
        events = game.apply_healing(c, 5)
        self.assertEqual(c.status, "ok")
        self.assertEqual(c.hp, 5)
        self.assertEqual(c.death_failures, 0)
        self.assertTrue(any("back on their feet" in e for e in events))

    def test_dead_needs_revive_flag(self):
        c = make_char(hp=0)
        c.status = "dead"
        game.apply_healing(c, 5)
        self.assertEqual(c.status, "dead")
        game.apply_healing(c, 5, revive=True)
        self.assertEqual(c.status, "ok")
        self.assertEqual(c.hp, 5)

    def test_death_save_progression(self):
        c = make_char(hp=0)
        c.status = "down"
        original = game.roll_die
        try:
            game.roll_die = RollQueue([15, 4, 1])  # success, failure, crit fail (x2)
            game.death_save(c)
            self.assertEqual(c.death_successes, 1)
            game.death_save(c)
            self.assertEqual(c.death_failures, 1)
            _, events = game.death_save(c)
            self.assertEqual(c.status, "dead")
            self.assertTrue(any("died" in e for e in events))
        finally:
            game.roll_die = original

    def test_death_save_nat20_and_stabilize(self):
        original = game.roll_die
        try:
            c = make_char(hp=0)
            c.status = "down"
            game.roll_die = RollQueue([20])
            game.death_save(c)
            self.assertEqual((c.status, c.hp), ("ok", 1))

            c2 = make_char(hp=0)
            c2.status = "down"
            game.roll_die = RollQueue([12, 14, 19])
            game.death_save(c2)
            game.death_save(c2)
            _, events = game.death_save(c2)
            self.assertEqual(c2.status, "stable")
            self.assertTrue(any("STABLE" in e for e in events))
        finally:
            game.roll_die = original

    def test_tpk_is_defeat(self):
        s = make_state(2)
        for c in s.party:
            c.status = "dead"
            c.hp = 0
        events = game.check_party_condition(s)
        self.assertTrue(s.game_over)
        self.assertEqual(s.outcome, "defeat")
        self.assertTrue(events)

    def test_mercy_rule_when_all_stable(self):
        s = make_state(2)
        for c in s.party:
            c.status = "stable"
            c.hp = 0
        events = game.check_party_condition(s)
        self.assertFalse(s.game_over)
        for c in s.party:
            self.assertEqual((c.status, c.hp), ("ok", 1))
        self.assertTrue(any("awaken" in e for e in events))
        self.assertTrue(any("NOTE" in h for h in s.history))


class RoundsAndActs(unittest.TestCase):
    def test_round_increments_when_all_heroes_acted(self):
        s = make_state(2)
        s.mark_acted("H1")
        self.assertEqual(s.round_num, 1)
        s.mark_acted("H2")
        self.assertEqual(s.round_num, 2)
        self.assertEqual(s.acted_this_round, [])

    def test_solo_round_increments_each_turn(self):
        s = make_state(1)
        s.mark_acted("H1")
        self.assertEqual(s.round_num, 2)

    def test_dead_heroes_not_required_for_round(self):
        s = make_state(3)
        s.party[2].status = "dead"
        s.mark_acted("H1")
        s.mark_acted("H2")
        self.assertEqual(s.round_num, 2)

    def test_advance_turn_skips_dead_and_stable_keeps_down(self):
        s = make_state(3)
        s.party[1].status = "dead"
        s.party[2].status = "down"
        s.active_index = 0
        s.advance_turn()
        self.assertEqual(s.active.name, "H3")  # skips dead H2, lands on dying H3
        s.advance_turn()
        self.assertEqual(s.active.name, "H1")

    def test_act_bands(self):
        self.assertIn("Act I", game.act_for_round(1, 24)[0])
        self.assertIn("Act II", game.act_for_round(12, 24)[0])
        self.assertIn("Act III", game.act_for_round(20, 24)[0])
        self.assertIn("FINALE", game.act_for_round(23, 24)[0])
        self.assertIn("EPILOGUE", game.act_for_round(30, 24)[0])


class SaveCompat(unittest.TestCase):
    def test_roundtrip_and_legacy(self):
        s = make_state(2, campaign_length=12)
        s.round_num = 5
        s.party[0].status = "down"
        s.party[0].hp = 0
        s.party[0].death_failures = 1
        with tempfile.TemporaryDirectory() as tmp:
            old_dir = game.SAVE_DIR
            game.SAVE_DIR = Path(tmp)
            try:
                game.save_game(s, "t")
                loaded = game.load_game("t")
                self.assertEqual(loaded.round_num, 5)
                self.assertEqual(loaded.campaign_length, 12)
                self.assertEqual(loaded.party[0].status, "down")
                self.assertEqual(loaded.party[0].death_failures, 1)

                # Legacy save without any of the new fields
                legacy = s.to_dict()
                for k in ("round_num", "campaign_length", "acted_this_round", "outcome"):
                    del legacy[k]
                for c in legacy["party"]:
                    for k in ("status", "death_successes", "death_failures"):
                        del c[k]
                old = game.GameState.from_dict(legacy)
                self.assertEqual(old.round_num, 1)
                self.assertEqual(old.campaign_length, 24)
                self.assertEqual(old.party[0].status, "down")  # hp 0 → down
                self.assertEqual(old.party[1].status, "ok")
            finally:
                game.SAVE_DIR = old_dir


class TurnEngine(unittest.TestCase):
    def setUp(self):
        self._propose = game.propose_check
        self._narrate = game.narrate
        self._narrate_party = game.narrate_party_round
        self._roll = game.roll_die
        self._call = game.call_ollama

    def tearDown(self):
        game.propose_check = self._propose
        game.narrate = self._narrate
        game.narrate_party_round = self._narrate_party
        game.roll_die = self._roll
        game.call_ollama = self._call

    def test_normal_action_applies_status_damage(self):
        game.propose_check = lambda *a, **k: {"needs_check": False, "reason": "talk"}
        game.narrate = lambda *a, **k: (
            "A goblin stabs H1!",
            {"updates": [{"name": "H1", "hp_delta": -4}], "location": None,
             "game_over": False},
        )
        s = make_state(1)
        out = game.run_action_turn(s, s.active, "talk to goblin", "m")
        self.assertEqual(out["kind"], "action")
        self.assertEqual(s.party[0].hp, 6)
        self.assertEqual(s.round_num, 2)  # solo hero acted → next round

    def test_failed_save_damage_applied_by_python(self):
        game.propose_check = lambda *a, **k: {
            "needs_check": True, "check_type": "save", "ability": "DEX",
            "dc": 30, "damage_on_fail": "1d4", "reason": "trap",
        }
        game.narrate = lambda *a, **k: ("The trap springs.", {})
        game.roll_die = RollQueue([5, 3])  # d20 fails vs 30, then 1d4=3
        s = make_state(1)
        out = game.run_action_turn(s, s.active, "open chest", "m")
        self.assertEqual(s.party[0].hp, 7)
        self.assertIn("damage", out["check"])

    def test_downed_actor_rolls_death_save_instead(self):
        s = make_state(1)
        s.party[0].status = "down"
        s.party[0].hp = 0
        game.roll_die = RollQueue([14])
        out = game.run_action_turn(s, s.party[0], "I stand up and fight!", "m")
        self.assertEqual(out["kind"], "death_save")
        self.assertEqual(s.party[0].death_successes, 1)

    def test_dead_actor_blocked(self):
        s = make_state(1)
        s.party[0].status = "dead"
        out = game.run_action_turn(s, s.party[0], "attack", "m")
        self.assertEqual(out["kind"], "blocked")

    def test_party_auto_locks_downed_while_others_plan(self):
        game.propose_check = lambda *a, **k: {"needs_check": False, "reason": "stub"}
        game.narrate_party_round = lambda *a, **k: ("Round resolves.", {})
        game.roll_die = RollQueue([14])
        s = make_state(2)
        s.party[0].status = "down"
        s.party[0].hp = 0
        out = game.run_action_turn(s, s.party[0], "ignored", "m")
        # H1 auto death-save locked; action applies to H2 (focused)
        self.assertEqual(out["kind"], "resolved")
        self.assertEqual(s.party[0].death_successes, 1)
        self.assertEqual(s.round_num, 2)

    def test_victory_sets_outcome(self):
        game.propose_check = lambda *a, **k: {"needs_check": False, "reason": "finale"}
        game.narrate = lambda *a, **k: (
            "You defeat the lich. The realm is saved!",
            {"updates": [], "location": None, "game_over": True, "victory": True},
        )
        s = make_state(1)
        game.run_action_turn(s, s.active, "strike the final blow", "m")
        self.assertTrue(s.game_over)
        self.assertEqual(s.outcome, "victory")

    def test_pass_turn_counts_and_death_saves(self):
        s = make_state(1)
        out = game.pass_turn(s)
        self.assertIn("passes", out["message"])
        self.assertEqual(s.round_num, 2)

        s2 = make_state(1)
        s2.party[0].status = "down"
        s2.party[0].hp = 0
        game.roll_die = RollQueue([3])
        out = game.pass_turn(s2)
        self.assertIn("clings to life", out["message"])
        self.assertEqual(s2.party[0].death_failures, 1)

    def test_party_locks_then_resolves_together(self):
        calls = {"propose": 0, "narrate_party": 0}
        game.propose_check = lambda *a, **k: (
            calls.__setitem__("propose", calls["propose"] + 1)
            or {"needs_check": False, "reason": "stub"}
        )
        game.narrate_party_round = lambda *a, **k: (
            calls.__setitem__("narrate_party", calls["narrate_party"] + 1)
            or ("The party moves as one.", {})
        )
        s = make_state(2)
        out = game.run_action_turn(s, s.party[0], "H1 sneaks ahead", "m")
        self.assertEqual(out["kind"], "planned")
        self.assertEqual(s.round_num, 1)
        self.assertEqual(calls["narrate_party"], 0)
        self.assertEqual(s.active.name, "H2")
        self.assertIn("H1", s.pending_actions)

        out = game.run_action_turn(s, s.party[1], "H2 covers the rear", "m")
        self.assertEqual(out["kind"], "resolved")
        self.assertEqual(calls["propose"], 2)
        self.assertEqual(calls["narrate_party"], 1)
        self.assertEqual(s.round_num, 2)
        self.assertEqual(s.pending_actions, {})
        self.assertIn("party moves", out["narrative"])

    def test_party_pass_skips_then_resolves(self):
        game.propose_check = lambda *a, **k: {"needs_check": False, "reason": "stub"}
        game.narrate_party_round = lambda *a, **k: ("One acts, one waits.", {})
        s = make_state(2)
        out = game.pass_turn(s, "m")
        self.assertEqual(out["kind"], "planned")
        self.assertEqual(s.pending_actions["H1"], "__pass__")
        out = game.run_action_turn(s, s.active, "I charge!", "m")
        self.assertEqual(out["kind"], "resolved")
        self.assertEqual(s.round_num, 2)

    def test_ask_dm_does_not_spend_turn(self):
        game.call_ollama = lambda *a, **k: "The wolf is still watching from the brush."
        s = make_state(2)
        s.pending_actions["H1"] = "sneak left"
        before_round = s.round_num
        before_pending = dict(s.pending_actions)
        out = game.ask_dm(s, "Is the wolf still watching us?", "m")
        self.assertEqual(out["kind"], "ask")
        self.assertIn("watching", out["narrative"])
        self.assertEqual(s.round_num, before_round)
        self.assertEqual(s.pending_actions, before_pending)
        self.assertTrue(any(h.startswith("ASK ") for h in s.history))
        self.assertTrue(any(h.startswith("DM (aside):") for h in s.history))

    def test_ask_dm_empty_blocked(self):
        s = make_state(1)
        out = game.ask_dm(s, "   ", "m")
        self.assertEqual(out["kind"], "blocked")


class WebApi(unittest.TestCase):
    PORT = 8791

    @classmethod
    def setUpClass(cls):
        cls._check = game.check_ollama
        cls._propose = game.propose_check
        cls._narrate = game.narrate
        game.check_ollama = lambda m: None
        game.propose_check = lambda *a, **k: {"needs_check": False, "reason": "stub"}
        game.narrate = lambda *a, **k: ("Stub narration.", {})
        cls.server = gui_web.ThreadingHTTPServer(("127.0.0.1", cls.PORT), gui_web.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        game.check_ollama = cls._check
        game.propose_check = cls._propose
        game.narrate = cls._narrate
        gui_web.STATE = None

    def post(self, path, body, expect_error=False):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.PORT}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            if not expect_error:
                raise
            return e.code, json.loads(e.read())

    def test_full_flow(self):
        hero = make_char("Web1").to_dict()
        status, d = self.post("/api/new", {
            "heroes": [hero], "world": "fantasy", "length": "short", "model": "x",
        })
        self.assertEqual(status, 200)
        self.assertEqual(d["state"]["campaign_length"], 12)
        self.assertEqual(d["state"]["round"], 1)
        self.assertIn("Act I", d["state"]["act"])

        status, d = self.post("/api/action", {"action": "look around", "model": "x"})
        self.assertEqual(status, 200)
        self.assertEqual(d["kind"], "action")
        self.assertFalse(d["ended"])
        self.assertEqual(d["state"]["round"], 2)

        # End the game via victory STATUS, then verify lockout
        game.narrate = lambda *a, **k: (
            "The end.", {"game_over": True, "victory": True})
        status, d = self.post("/api/action", {"action": "win", "model": "x"})
        self.assertTrue(d["ended"])
        self.assertEqual(d["state"]["outcome"], "victory")
        self.assertIn("VICTORY", d["ending"])

        status, d = self.post("/api/action", {"action": "keep going", "model": "x"},
                              expect_error=True)
        self.assertEqual(status, 409)
        status, d = self.post("/api/pass", {}, expect_error=True)
        self.assertEqual(status, 409)
        game.narrate = lambda *a, **k: ("Stub narration.", {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
