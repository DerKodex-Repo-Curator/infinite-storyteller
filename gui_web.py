#!/usr/bin/env python3
"""Reliable local web UI for Infinite Storyteller.

Uses only Python's standard library. The browser talks only to localhost;
Ollama and game.py remain the rules/narrative backend.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

import game

HOST = "127.0.0.1"
PORT = 8765
DEFAULT_MODEL = game.DEFAULT_MODEL
STATE: Optional[game.GameState] = None
LOCK = threading.Lock()


def resolve_model(requested: Any) -> str:
    name = str(requested or "").strip()
    if name:
        return name
    return game.pick_default_model()

WORLDS = {
    "fantasy": (
        "the Whispering Forest edge",
        "A dark-fantasy realm of cursed woods, bloodied relics, and a ruined keep "
        "that still whispers names.",
    ),
    "cyberpunk": (
        "Sector 9 rooftops",
        "A rainy neon megacity after a city-wide blackout. Corpses of drones, "
        "black-market tech, and gangs that harvest memory chips from the dead.",
    ),
    "space": (
        "Freighter Erebus, Deck C",
        "A deep-space freighter weeks from port. The crew is thinning. Something "
        "in the cargo bay changes the emergency lights when it listens.",
    ),
}


def forge_character(data: Dict[str, Any], model: str) -> game.Character:
    name = str(data.get("name", "")).strip()
    vibe = str(data.get("vibe", "")).strip() or "a bold adventurer"
    archetype = str(data.get("archetype", "")).strip()
    taken = ", ".join(str(x) for x in data.get("taken", [])) or "(none)"
    prompt = f"""
You are helping create a Dungeons & Dragons level-1 character for a teen-friendly dark fantasy game.

Player vibe: {vibe}
Name preference: {name or "(invent a cool fitting name)"}
Archetype preference: {archetype or "(pick the best fit)"}
Names already taken (do not reuse): {taken}

Allowed archetypes: Fighter, Rogue, Wizard, Cleric, Ranger, Barbarian.
Assign ability scores using the standard array 15,14,13,12,10,8, each once.

Return ONLY valid JSON:
{{
  "name": "string",
  "archetype": "Fighter|Rogue|Wizard|Cleric|Ranger|Barbarian",
  "concept": "one short sentence",
  "personality": "one short sentence",
  "abilities": {{"STR":0,"DEX":0,"CON":0,"INT":0,"WIS":0,"CHA":0}},
  "proficient_skills": ["Skill Name"],
  "inventory": ["item"]
}}
"""
    raw = game.call_ollama(prompt, model, temperature=0.7, num_predict=550)
    parsed = game.extract_json_object(raw) or {}
    character = game.normalize_character(parsed, name or "Hero")
    if name:
        character.name = name
    return character


def character_json(c: game.Character) -> Dict[str, Any]:
    data = c.to_dict()
    data["sheet"] = c.sheet_text()
    return data


def state_json(state: game.GameState) -> Dict[str, Any]:
    waiting = [c.name for c in state.heroes_who_must_plan()]
    return {
        "location": state.location,
        "active": state.active.name,
        "game_over": state.game_over,
        "outcome": state.outcome,
        "round": state.round_num,
        "campaign_length": state.campaign_length,
        "act": game.act_for_round(state.round_num, state.campaign_length)[0],
        "model": state.model or DEFAULT_MODEL,
        "party_mode": state.is_party_mode,
        "waiting": waiting,
        "pending": game.pending_summary(state),
        "encounter": game.encounter_json(state),
        "party": [character_json(c) for c in state.party],
    }


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Infinite Storyteller</title>
<style>
:root{--bg:#171719;--panel:#242427;--panel2:#302f32;--ink:#f7f1e8;--muted:#b8b1a8;
--accent:#e8793a;--accent2:#ff9d66;--line:#4c4946;--gold:#f5c451;--blue:#8bc5ff;--bad:#ff8a8a}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}
header{padding:22px 28px 14px;border-bottom:1px solid var(--line);
background:linear-gradient(180deg,#211f20,var(--bg))}
h1{margin:0;color:var(--accent2);font-family:Georgia,serif;font-size:30px}
header p{margin:5px 0 0;color:var(--muted)} main{max-width:1040px;margin:0 auto;padding:24px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:26px;
box-shadow:0 10px 30px #0005}.hidden{display:none!important}
h2{margin:0 0 10px;font-size:24px} h3{margin:18px 0 8px} p{line-height:1.5}
.muted{color:var(--muted)} .steps{line-height:1.8;color:var(--muted)}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.actions{margin-top:22px}
button{border:1px solid var(--line);border-radius:9px;padding:11px 16px;background:var(--panel2);
color:var(--ink);font-size:15px;font-weight:600;cursor:pointer}
button:hover{border-color:#777;background:#3b393c} button.primary{background:var(--accent);
border-color:var(--accent);color:#1b0d06} button.primary:hover{background:var(--accent2)}
button:disabled{opacity:.45;cursor:wait}
label{display:block;font-size:13px;font-weight:700;color:var(--muted);margin:15px 0 6px;
text-transform:uppercase;letter-spacing:.05em}
input,textarea,select{width:100%;background:#fff;color:#111;border:3px solid #8d8985;
border-radius:8px;padding:12px;font:16px inherit;outline:none}
input:focus,textarea:focus,select:focus{border-color:var(--accent2)}
textarea{min-height:85px;resize:vertical}.radio-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}
.radio{display:flex;align-items:center;gap:10px;background:var(--panel2);border:1px solid var(--line);
padding:11px;border-radius:8px;color:var(--ink);font-weight:500;text-transform:none;letter-spacing:0;margin:0}
.radio input{width:auto;accent-color:var(--accent)}
.sheet{white-space:pre-wrap;font:14px/1.55 Menlo,monospace;background:#111;border:1px solid var(--line);
padding:16px;border-radius:8px;margin:14px 0;overflow:auto}
.busy{padding:12px;border-left:4px solid var(--gold);background:#3b3421;color:#ffe6a0;margin:14px 0}
.error{padding:12px;border-left:4px solid var(--bad);background:#3c2222;color:#ffd0d0;margin:14px 0}
#game{display:grid;grid-template-columns:minmax(0,1fr) 275px;gap:16px}
#story{height:520px;overflow-y:auto;background:#0d0d0f;border:1px solid var(--line);
border-radius:11px;padding:20px;font:17px/1.65 Georgia,serif}
.beat{margin-bottom:20px;white-space:pre-wrap}.player{color:var(--blue);font-family:system-ui,sans-serif}
.ask{color:#9ad7c2;font-family:system-ui,sans-serif}.dice{color:var(--gold);font-family:Menlo,monospace;font-size:14px}.meta{color:#aaa;font:13px Menlo,monospace}
.party{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:16px}
.hero{padding:11px 0;border-bottom:1px solid var(--line)}.hero:last-child{border:0}
.hero.active strong{color:var(--accent2)}.commandbar{margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.commandbar input{flex:1;min-width:220px}.toolbar{margin:12px 0;display:flex;gap:7px;flex-wrap:wrap}
.toolbar button{padding:8px 11px;font-size:13px}
.btn-legend{margin:0 0 10px;padding:10px 12px;background:var(--panel2);border:1px solid var(--line);
border-radius:8px;font-size:13px;line-height:1.55;color:var(--muted)}
.btn-legend strong{color:var(--ink);font-weight:700}
.modelbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:10px 0 0}
.modelbar label{margin:0;text-transform:none;letter-spacing:0;font-size:13px;color:var(--muted);font-weight:600}
.modelbar select{width:auto;min-width:280px;background:#fff;color:#111;border:3px solid #8d8985;
border-radius:8px;padding:8px 10px;font:14px inherit}
.play-model{margin:10px 0 0;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.play-model label{margin:0;text-transform:none;letter-spacing:0;font-size:13px;color:var(--muted);font-weight:700}
.play-model select{width:auto;min-width:280px;background:#fff;color:#111;border:3px solid #8d8985;
border-radius:8px;padding:8px 10px;font:14px inherit}
dialog{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:12px;
max-width:700px;width:calc(100% - 30px);padding:24px}dialog::backdrop{background:#000b}
@media(max-width:760px){#game{grid-template-columns:1fr}.radio-grid{grid-template-columns:1fr}
#story{height:430px}main{padding:14px}}
</style>
</head>
<body>
<header><h1>The Infinite Storyteller</h1><p>Offline D&amp;D adventure · local Ollama DM · Web UI v1</p>
<div class="modelbar">
  <label for="modelSelect">Local AI model</label>
  <select id="modelSelect" aria-label="Local AI model"></select>
</div>
</header>
<main>
  <section id="welcome" class="card">
    <h2>Welcome</h2>
    <p>This is a text adventure for 1–4 players on this Mac.</p>
    <div class="steps">1. Create each hero — you provide a name and 1–2 sentence description<br>
    2. Pick a world<br>3. Type actions; Python rolls dice and the local AI narrates</div>
    <div id="ready" class="busy">Checking Ollama…</div>
    <div class="row actions">
      <button class="primary" onclick="showParty()">Start a new adventure</button>
      <button onclick="showLoads()">Load a saved game</button>
      <button onclick="showHelp()">How to play</button>
    </div>
  </section>

  <section id="partyStep" class="card hidden">
    <h2>Step 1 of 3 — Party size</h2>
    <p class="muted">How many players will share this laptop?</p>
    <div class="radio-grid">
      <label class="radio"><input type="radio" name="count" value="1" checked>1 player</label>
      <label class="radio"><input type="radio" name="count" value="2">2 players</label>
      <label class="radio"><input type="radio" name="count" value="3">3 players</label>
      <label class="radio"><input type="radio" name="count" value="4">4 players</label>
    </div>
    <div class="row actions"><button onclick="screen('welcome')">Back</button>
    <button class="primary" onclick="beginHeroes()">Next: create heroes</button></div>
  </section>

  <section id="heroStep" class="card hidden">
    <h2 id="heroTitle">Step 2 of 3 — Create hero</h2>
    <p class="muted">These fields are part of character creation. Name is optional; the description is your 1–2 sentence idea.</p>
    <label for="heroName">Character name (optional)</label>
    <input id="heroName" placeholder="Example: Nyx — or leave blank for AI">
    <label for="heroVibe">Describe your character in 1–2 sentences</label>
    <textarea id="heroVibe" placeholder="Example: A smug half-elf thief who jokes when frightened but never abandons a friend."></textarea>
    <label for="heroClass">Archetype</label>
    <select id="heroClass">
      <option value="">Let the AI choose</option><option>Fighter</option><option>Rogue</option>
      <option>Wizard</option><option>Cleric</option><option>Ranger</option><option>Barbarian</option>
    </select>
    <div id="heroMessage"></div>
    <div class="row actions"><button onclick="showParty()">Back</button>
    <button id="forgeBtn" class="primary" onclick="forge()">Forge character with AI</button></div>
  </section>

  <section id="reviewStep" class="card hidden">
    <h2>Review your hero</h2><div id="sheet" class="sheet"></div>
    <div class="row actions"><button onclick="screen('heroStep')">Try again</button>
    <button class="primary" onclick="keepHero()">Keep hero</button></div>
  </section>

  <section id="worldStep" class="card hidden">
    <h2>Step 3 of 3 — Choose a world</h2>
    <div class="radio-grid">
      <label class="radio"><input type="radio" name="world" value="fantasy" checked>Cursed forest &amp; ruined keep</label>
      <label class="radio"><input type="radio" name="world" value="cyberpunk">Neon cyberpunk city</label>
      <label class="radio"><input type="radio" name="world" value="space">Haunted space freighter</label>
      <label class="radio"><input type="radio" name="world" value="custom">Custom world</label>
    </div>
    <label for="customWorld">Custom world description (only if Custom selected)</label>
    <textarea id="customWorld" placeholder="Describe the world…"></textarea>
    <label for="customLocation">Custom starting location</label>
    <input id="customLocation" value="an unfamiliar threshold">
    <label>Adventure length (1 round = every hero takes a turn)</label>
    <div class="radio-grid">
      <label class="radio"><input type="radio" name="length" value="short">Short — ~12 rounds</label>
      <label class="radio"><input type="radio" name="length" value="standard" checked>Standard — ~24 rounds</label>
      <label class="radio"><input type="radio" name="length" value="long">Long — ~40 rounds</label>
    </div>
    <p class="muted">The story builds through acts and reaches a final boss, puzzle, or showdown near the end.</p>
    <div id="worldMessage"></div>
    <div class="row actions"><button class="primary" onclick="startGame()">Begin adventure</button></div>
  </section>

  <section id="gameScreen" class="hidden">
    <div id="game">
      <div><div id="story"></div>
        <div class="toolbar">
          <button onclick="showHelp()" title="Open a short guide to how the game works.">Help</button>
          <button onclick="showSheets()" title="View every hero's full character sheet (stats, skills, gear).">Sheets</button>
          <button onclick="askDM()"
            title="Ask the DM a clarifying question. Free — does not spend a turn or lock in an action. Type your question in the box first.">Ask DM</button>
          <button onclick="doAction('I take a careful look around.')"
            title="Spend this hero's action carefully surveying the scene. In party play, this locks Look in for the active hero.">Look</button>
          <button onclick="passTurn()"
            title="Skip this hero's action for the round. In party play they wait while others act; dying heroes roll a death save instead.">Pass</button>
          <button onclick="saveGame()" title="Save the adventure to a named slot so you can load it later.">Save</button>
          <button onclick="confirmMenu()" title="Return to the main menu. Unsaved progress may be lost — save first!">Menu</button>
        </div>
        <div class="btn-legend" id="btnLegend">
          <strong>Help</strong> — how to play ·
          <strong>Sheets</strong> — view character stats ·
          <strong>Ask DM</strong> — free clarifying question (no turn spent) ·
          <strong>Look</strong> — carefully survey the scene (uses this hero's action) ·
          <strong>Pass</strong> — skip this hero's action this round ·
          <strong>Save</strong> — save the adventure ·
          <strong>Menu</strong> — leave to the title screen (save first!)
        </div>
        <div class="play-model">
          <label for="gameModelSelect">Model for next reply</label>
          <select id="gameModelSelect" aria-label="Model for next reply" title="Choose which local AI model narrates the next reply."></select>
        </div>
        <div class="commandbar"><input id="action" placeholder="Type what your hero does…" title="Describe what the active hero tries to do.">
        <button id="doBtn" class="primary" onclick="submitAction()" title="Submit this hero's action.">Do it</button></div>
        <div id="planHint" class="muted" style="margin-top:8px"></div>
        <div id="gameMessage"></div>
      </div>
      <aside id="partyPanel" class="party"></aside>
    </div>
  </section>
</main>

<dialog id="modal"><div id="modalBody"></div><div class="row actions">
<button class="primary" onclick="document.getElementById('modal').close()">Close</button></div></dialog>

<script>
const ids=['welcome','partyStep','heroStep','reviewStep','worldStep','gameScreen'];
let count=1, heroIndex=0, heroes=[], pending=null, state=null, models=[], selectedModel='qwen2.5:14b', endedShown=false;
function screen(id){ids.forEach(x=>document.getElementById(x).classList.toggle('hidden',x!==id));}
async function api(path,body=null){
  const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},
    body:body?JSON.stringify(body):null});
  const d=await r.json(); if(!r.ok) throw new Error(d.error||'Request failed'); return d;
}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',
  '"':'&quot;',"'":'&#39;'}[c]));}
function fillModelSelects(){
  ['modelSelect','gameModelSelect'].forEach(id=>{
    const el=document.getElementById(id); if(!el) return;
    el.innerHTML=models.map(m=>`<option value="${esc(m.id)}">${esc(m.label)}</option>`).join('');
    el.value=selectedModel;
  });
}
function syncModelFrom(el){
  selectedModel=el.value;
  fillModelSelects();
  if(state) state.model=selectedModel;
}
function showParty(){screen('partyStep');}
function beginHeroes(){
  count=Number(document.querySelector('input[name=count]:checked').value); heroIndex=0; heroes=[];
  prepareHero();
}
function prepareHero(){
  document.getElementById('heroTitle').textContent=`Step 2 of 3 — Create hero ${heroIndex+1} of ${count}`;
  document.getElementById('heroName').value='';
  document.getElementById('heroVibe').value='';
  document.getElementById('heroClass').value='';
  document.getElementById('heroMessage').innerHTML='';
  screen('heroStep'); setTimeout(()=>document.getElementById('heroName').focus(),50);
}
async function forge(){
  const vibe=document.getElementById('heroVibe').value.trim();
  if(!vibe){document.getElementById('heroMessage').innerHTML='<div class="error">Please describe your character in 1–2 sentences.</div>';return;}
  const b=document.getElementById('forgeBtn');b.disabled=true;
  document.getElementById('heroMessage').innerHTML=`<div class="busy">Forging with ${esc(selectedModel)}… usually 10–20 seconds.</div>`;
  try{
    pending=await api('/api/character',{name:document.getElementById('heroName').value,vibe,
      archetype:document.getElementById('heroClass').value,taken:heroes.map(h=>h.name),model:selectedModel});
    document.getElementById('sheet').textContent=pending.sheet;screen('reviewStep');
  }catch(e){document.getElementById('heroMessage').innerHTML=`<div class="error">${esc(e.message)}</div>`}
  finally{b.disabled=false}
}
function keepHero(){heroes.push(pending);heroIndex++;if(heroIndex<count)prepareHero();else screen('worldStep');}
async function startGame(){
  let world=document.querySelector('input[name=world]:checked').value;
  const length=document.querySelector('input[name=length]:checked').value;
  const custom=document.getElementById('customWorld').value.trim();
  if(world==='custom'&&!custom){document.getElementById('worldMessage').innerHTML='<div class="error">Describe your custom world.</div>';return;}
  document.getElementById('worldMessage').innerHTML=`<div class="busy">Writing opening scene with ${esc(selectedModel)}…</div>`;
  try{
    const d=await api('/api/new',{heroes,world,custom,length,location:document.getElementById('customLocation').value,model:selectedModel});
    state=d.state;selectedModel=state.model||selectedModel;fillModelSelects();endedShown=false;
    document.getElementById('story').innerHTML='';
    screen('gameScreen');renderState();addBeat(d.narrative,'dm');
    if(state.party_mode){
      addBeat('Party planning: each hero locks in an action. The story continues only after everyone has locked in (or skipped).','meta');
    }else{
      addBeat('Your turn — type an action in the white box below.','meta');
    }
    document.getElementById('action').focus();
  }catch(e){document.getElementById('worldMessage').innerHTML=`<div class="error">${esc(e.message)}</div>`}
}
function addBeat(text,kind){const d=document.createElement('div');d.className='beat '+kind;d.textContent=text;
  const s=document.getElementById('story');s.appendChild(d);s.scrollTop=s.scrollHeight;}
function statusIcon(h){return h.status==='dead'?'💀 ':h.status==='down'?'🩸 ':h.status==='stable'?'💤 ':'';}
function statusNote(h){
  if(h.status==='down')return `<span style="color:var(--bad)">DYING — death saves ${h.death_successes||0}✓/${h.death_failures||0}✗</span><br>`;
  if(h.status==='stable')return '<span class="muted">Unconscious (stable) — needs healing</span><br>';
  if(h.status==='dead')return '<span style="color:var(--bad)">DEAD</span><br>';
  return '';
}
function planFor(name){
  const row=(state.pending||[]).find(p=>p.name===name);
  return row?row.plan:'';
}
function renderState(){
  if(!state)return;
  if(state.model){selectedModel=state.model;fillModelSelects();}
  const waiting=state.waiting||[];
  const enc=state.encounter;
  let encHtml='';
  if(enc&&enc.foes){
    encHtml=`<h3 style="margin-top:14px">⚔ ${esc(enc.title)}</h3>
      <p class="muted" style="margin:0 0 8px">${enc.living} alive / ${enc.total} in this fight (fixed roster)</p>`+
      enc.foes.map(f=>`<div class="hero" style="padding:6px 0"><strong>${f.alive?'':'☠ '}${esc(f.name)}</strong>
        <span class="muted"> ${esc(f.id)}</span><br>
        ${f.alive?`HP ${f.hp}/${f.max_hp} · AC ${f.ac}`:'defeated'}</div>`).join('');
  }
  document.getElementById('partyPanel').innerHTML=`<h3>📍 ${esc(state.location)}</h3>
    <p class="muted" style="margin:0 0 4px">⏳ Round ${state.round}/${state.campaign_length} · ${esc(state.act||'')}</p>
    <p class="muted" style="margin:0 0 10px">Model: ${esc(state.model||selectedModel)}</p>`+
    (state.party_mode?`<p class="muted" style="margin:0 0 10px">Party planning: everyone locks in, then one shared outcome.</p>`:'')+
    state.party.map(h=>{
      const plan=planFor(h.name);
      let planLine='';
      if(state.party_mode){
        if(plan==='waiting') planLine='<span style="color:var(--gold)">⏳ writing action…</span><br>';
        else if(plan==='skipping') planLine='<span class="muted">✓ skipping this round</span><br>';
        else if(plan==='death save') planLine='<span class="muted">✓ death save ready</span><br>';
        else if(plan&&plan!=='ok'&&plan!=='down'&&plan!=='stable'&&plan!=='dead')
          planLine=`<span style="color:#9dffa8">✓ ${esc(plan)}</span><br>`;
      }
      return `<div class="hero ${h.name===state.active?'active':''}"><strong>${statusIcon(h)}${esc(h.name)}</strong><br>
      ${statusNote(h)}${planLine}${esc(h.archetype)} · HP ${h.hp}/${h.max_hp} · AC ${h.ac}<br><span class="muted">${esc(h.inventory.join(', '))}</span></div>`;
    }).join('')+encHtml;
  const btn=document.getElementById('doBtn');
  const input=document.getElementById('action');
  const hint=document.getElementById('planHint');
  if(state.party_mode && !state.game_over){
    const last=waiting.length<=1;
    btn.textContent=last?'Lock in & resolve':'Lock in';
    btn.title=last
      ?'Lock in this hero\'s action and resolve the whole party\'s round together.'
      :'Lock in this hero\'s action. The story waits until every living hero has locked in or passed.';
    input.placeholder=`${state.active}'s action this round…`;
    hint.textContent=waiting.length
      ? `Hot-seat: ${state.active} types first. Still waiting for: ${waiting.join(', ')}.`
      : 'Everyone locked in.';
    document.getElementById('btnLegend').innerHTML=
      `<strong>Help</strong> — how to play ·
       <strong>Sheets</strong> — view character stats ·
       <strong>Ask DM</strong> — free clarifying question (no turn) ·
       <strong>Look</strong> — lock in a careful survey for <em>${esc(state.active)}</em> ·
       <strong>Pass</strong> — ${esc(state.active)} skips this round (others still act) ·
       <strong>Save</strong> — save the adventure ·
       <strong>Menu</strong> — leave to the title screen (save first!) ·
       <strong>${esc(btn.textContent)}</strong> — ${last?'submit the last plan and continue the story':'save this plan and hand the keyboard to the next player'}`;
  }else{
    btn.textContent='Do it';
    btn.title='Submit this hero\'s action and continue the story.';
    input.placeholder='Type what your hero does…';
    hint.textContent='';
    document.getElementById('btnLegend').innerHTML=
      `<strong>Help</strong> — how to play ·
       <strong>Sheets</strong> — view character stats ·
       <strong>Ask DM</strong> — free clarifying question (no turn) ·
       <strong>Look</strong> — carefully survey the scene (uses this turn) ·
       <strong>Pass</strong> — skip this turn ·
       <strong>Save</strong> — save the adventure ·
       <strong>Menu</strong> — leave to the title screen (save first!) ·
       <strong>Do it</strong> — submit the action in the box`;
  }
  setEnded(!!state.game_over);
}
function setEnded(ended){
  document.getElementById('action').disabled=ended;
  document.getElementById('doBtn').disabled=ended;
  if(ended&&!endedShown){
    endedShown=true;
    addBeat(state.outcome==='victory'?'🏆 VICTORY — the tale is complete! Use Menu to start a new adventure.':
      state.outcome==='defeat'?'💀 DEFEAT — the party has fallen. Use Menu to start a new adventure.':
      '— The tale ends. Use Menu to start a new adventure. —','meta');
  }
}
function showEvents(d){(d.events||[]).forEach(e=>addBeat(e,'meta'));}
function showChecks(d){
  if(d.checks&&d.checks.length){d.checks.forEach(c=>{if(c) addBeat(c,'dice');});}
  else if(d.check) addBeat(d.check,'dice');
}
async function submitAction(){const a=document.getElementById('action');const text=a.value.trim();if(!text)return;a.value='';await doAction(text);}
async function doAction(text){
  selectedModel=document.getElementById('gameModelSelect').value||selectedModel;
  addBeat(`[${state.active}] ${text}`,'player');const b=document.getElementById('doBtn');b.disabled=true;
  const waitingBefore=(state.waiting||[]).length;
  const willResolve=state.party_mode && waitingBefore<=1;
  document.getElementById('gameMessage').innerHTML=willResolve
    ?`<div class="busy">Everyone locked in — resolving the party's round with ${esc(selectedModel)}…</div>`
    :`<div class="busy">${state.party_mode?'Locking in…':'Using '+esc(selectedModel)+' — consulting rules / rolling dice…'}</div>`;
  try{const d=await api('/api/action',{action:text,model:selectedModel});
    if(d.message) addBeat(d.message,'meta');
    if(d.kind==='planned'){state=d.state;renderState();}
    else{
      showChecks(d);
      if(d.kind==='action' && !d.check && !d.checks?.length && d.reason)
        addBeat(`No roll needed — ${d.reason}`,'meta');
      if(d.narrative) addBeat(d.narrative,'dm');
      showEvents(d);state=d.state;renderState();
    }}
  catch(e){addBeat(`Error: ${e.message}`,'meta')}finally{b.disabled=state&&state.game_over;document.getElementById('gameMessage').innerHTML='';document.getElementById('action').focus()}
}
async function passTurn(){
  if(state&&state.game_over)return;
  selectedModel=document.getElementById('gameModelSelect').value||selectedModel;
  const waitingBefore=(state.waiting||[]).length;
  const willResolve=state.party_mode && waitingBefore<=1;
  document.getElementById('gameMessage').innerHTML=willResolve
    ?`<div class="busy">Resolving the party's round…</div>`:'';
  try{const d=await api('/api/pass',{model:selectedModel});state=d.state;
    if(d.message) addBeat(d.message,'meta');
    if(d.kind!=='planned'){
      showChecks(d);if(d.narrative) addBeat(d.narrative,'dm');showEvents(d);
    }
    renderState();}
  catch(e){addBeat(`Error: ${e.message}`,'meta')}
  finally{document.getElementById('gameMessage').innerHTML='';}
}
async function askDM(){
  if(state&&state.game_over)return;
  const box=document.getElementById('action');
  let q=box.value.trim();
  if(!q){
    q=prompt('Ask the DM a clarifying question (free — does not spend a turn):','');
    if(!q||!q.trim())return;
    q=q.trim();
  }else{
    box.value='';
  }
  selectedModel=document.getElementById('gameModelSelect').value||selectedModel;
  addBeat(`❓ [${state.active} asks] ${q}`,'ask');
  document.getElementById('gameMessage').innerHTML=`<div class="busy">Asking the DM (${esc(selectedModel)}) — no turn spent…</div>`;
  const b=document.getElementById('doBtn');b.disabled=true;
  try{
    const d=await api('/api/ask',{question:q,model:selectedModel});
    if(d.message) addBeat(d.message,'meta');
    if(d.narrative) addBeat(d.narrative,'dm');
    state=d.state;renderState();
  }catch(e){addBeat(`Error: ${e.message}`,'meta')}
  finally{b.disabled=state&&state.game_over;document.getElementById('gameMessage').innerHTML='';box.focus()}
}
async function saveGame(){const slot=prompt('Save slot name:','quicksave');if(!slot)return;
  try{const d=await api('/api/save',{slot,model:selectedModel});addBeat(d.message,'meta')}catch(e){alert(e.message)}}
function confirmMenu(){
  if(state && !state.game_over){
    if(!confirm('Return to the menu? Unsaved progress will be lost if you have not saved.')) return;
  }
  screen('welcome');
}
async function showLoads(){try{const d=await api('/api/saves');if(!d.saves.length){alert('No saves yet. Start a new adventure first.');return;}
  const slot=prompt('Type a save name:\\n'+d.saves.join('\\n'),d.saves[0]);if(!slot)return;
  const x=await api('/api/load',{slot});state=x.state;selectedModel=state.model||selectedModel;fillModelSelects();endedShown=false;
  screen('gameScreen');document.getElementById('story').innerHTML='';
  addBeat(`Save loaded · model set to ${selectedModel}.`,'meta');if(x.last) addBeat(x.last,'dm');renderState();}catch(e){alert(e.message)}}
function showHelp(){document.getElementById('modalBody').innerHTML=`<h2>How to play</h2><p>Type actions in plain English.
  Python decides checks and rolls dice; the local AI narrates. Use the model dropdown before an action to switch local models.</p>
  <p><strong>Ask DM:</strong> type a clarifying question in the box, then click <em>Ask DM</em>.
  This is free — it does not spend a turn or lock in a party action. Use it to clarify
  what you see, hear, or remember. Use <em>Do it / Lock in</em> for real in-world actions.</p>
  <p><strong>Party play (2–4):</strong> everyone locks in their action first. The DM resolves the whole party's round together.
  Pass means that hero skips this round. Solo play still resolves immediately.</p>
  <p><strong>Rounds &amp; acts:</strong> when every hero has taken a turn, one round passes. The story builds through
  acts toward a final boss or showdown near the chosen adventure length.</p>
  <p><strong>Going down:</strong> at 0 HP a hero is dying and rolls death saves on their turns
  (10+ succeeds; 3 successes = stable, 3 failures = dead). Allies can stabilize (Medicine DC 10) or heal them.
  A natural 20 springs them back up with 1 HP!</p>
  <div class="sheet">Help · Sheets · Ask DM · Look · Pass · Save
  \\nAsk examples:\\n• What does the carved symbol on the door look like?\\n• Can I tell if the wolf is still watching us?
  \\nAction examples:\\n• search the room for traps\\n• persuade the guard\\n• attack the goblin with my sword</div>`;
  document.getElementById('modal').showModal();}
function showSheets(){document.getElementById('modalBody').innerHTML='<h2>Character sheets</h2>'+
  state.party.map(h=>`<div class="sheet">${esc(h.sheet)}</div>`).join('');document.getElementById('modal').showModal();}
document.getElementById('action').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();submitAction()}});
document.getElementById('modelSelect').addEventListener('change',e=>syncModelFrom(e.target));
document.getElementById('gameModelSelect').addEventListener('change',e=>syncModelFrom(e.target));
async function checkStatus(attempt){
  const el=document.getElementById('ready');
  try{
    const d=await api('/api/status');
    models=d.models||[]; selectedModel=d.model||selectedModel; fillModelSelects();
    el.textContent=`✓ Ollama ready · default ${d.model}`; el.className='busy';
  }catch(e){
    if(attempt<40){
      el.textContent=`⏳ Waiting for Ollama to start… (${attempt+1}) — this can take ~30s after a reboot`;
      el.className='busy';
      setTimeout(()=>checkStatus(attempt+1),2000);
    }else{
      el.textContent='✗ '+e.message+' — open the Ollama app, then reload this page.';
      el.className='error';
    }
  }
}
checkStatus(0);
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[web] {fmt % args}")

    def send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/":
                raw = HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
            elif path == "/api/status":
                installed = game.list_ollama_models()
                if not installed:
                    raise SystemExit("Ollama not ready")
                model = game.pick_default_model(installed)
                game.check_ollama(model)
                self.send_json({
                    "ok": True,
                    "model": model,
                    "models": game.model_choices(installed),
                    "installed": installed,
                })
            elif path == "/api/saves":
                self.send_json({"saves": [p.stem for p in game.list_saves()]})
            else:
                self.send_json({"error": "Not found"}, 404)
        except SystemExit:
            self.send_json({"error": "Ollama or model is not ready"}, 503)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        global STATE
        path = urllib.parse.urlparse(self.path).path
        try:
            data = self.read_json()
            model = resolve_model(data.get("model"))
            if path == "/api/character":
                game.check_ollama(model)
                self.send_json(character_json(forge_character(data, model)))
                return
            if path == "/api/new":
                party = [game.Character.from_dict(x) for x in data.get("heroes", [])]
                if not party:
                    raise ValueError("Create at least one hero")
                key = str(data.get("world", "fantasy"))
                if key == "custom":
                    setting = str(data.get("custom", "")).strip()
                    location = str(data.get("location", "")).strip() or "an unfamiliar threshold"
                    if not setting:
                        raise ValueError("Custom world description is required")
                else:
                    location, setting = WORLDS.get(key, WORLDS["fantasy"])
                length = str(data.get("length", "standard")).lower()
                rounds = game.ADVENTURE_LENGTHS.get(length, 24)
                game.check_ollama(model)
                with LOCK:
                    STATE = game.GameState(
                        party=party, location=location, setting=setting,
                        model=model, campaign_length=rounds,
                    )
                    narrative, update = game.narrate(STATE, STATE.active, None, model)
                    events = game.apply_status(STATE, update)
                    STATE.history.append(f"DM: {narrative}")
                self.send_json({
                    "narrative": narrative,
                    "events": events,
                    "state": state_json(STATE),
                })
                return
            if path == "/api/action":
                if STATE is None:
                    raise ValueError("No active adventure")
                action = str(data.get("action", "")).strip()
                if not action:
                    raise ValueError("Type an action")
                with LOCK:
                    if STATE.game_over:
                        self.send_json({
                            "error": "The adventure has ended. Start a new one or load a save.",
                        }, 409)
                        return
                    STATE.model = model
                    game.check_ollama(model)
                    game.auto_lock_downed(STATE)
                    if STATE.is_party_mode:
                        STATE.focus_next_planner()
                        actor = STATE.active
                    else:
                        if STATE.active.status in ("stable", "dead"):
                            STATE.advance_turn()
                        actor, action = game.parse_actor_prefix(STATE, action)
                    result = game.run_action_turn(STATE, actor, action, model)
                self.send_json({
                    "kind": result["kind"],
                    "message": result["message"],
                    "check": result.get("check", ""),
                    "checks": result.get("checks") or [],
                    "reason": result.get("reason", ""),
                    "narrative": result.get("narrative", ""),
                    "events": result.get("events") or [],
                    "ended": STATE.game_over,
                    "ending": game.ending_text(STATE) if STATE.game_over else "",
                    "state": state_json(STATE),
                })
                return
            if path == "/api/ask":
                if STATE is None:
                    raise ValueError("No active adventure")
                question = str(data.get("question", "")).strip()
                if not question:
                    raise ValueError("Type a question for the DM")
                with LOCK:
                    if STATE.game_over:
                        self.send_json({
                            "error": "The adventure has ended. Start a new one or load a save.",
                        }, 409)
                        return
                    STATE.model = model
                    game.check_ollama(model)
                    result = game.ask_dm(STATE, question, model, asker=STATE.active)
                self.send_json({
                    "kind": result["kind"],
                    "message": result.get("message", ""),
                    "narrative": result.get("narrative", ""),
                    "state": state_json(STATE),
                })
                return
            if path == "/api/pass":
                if STATE is None:
                    raise ValueError("No active adventure")
                with LOCK:
                    if STATE.game_over:
                        self.send_json({
                            "error": "The adventure has ended. Start a new one or load a save.",
                        }, 409)
                        return
                    STATE.model = model
                    result = game.pass_turn(STATE, model)
                self.send_json({
                    "kind": result.get("kind", "pass"),
                    "message": result["message"],
                    "check": result.get("check", ""),
                    "checks": result.get("checks") or [],
                    "narrative": result.get("narrative", ""),
                    "events": result.get("events") or [],
                    "ended": STATE.game_over,
                    "ending": game.ending_text(STATE) if STATE.game_over else "",
                    "state": state_json(STATE),
                })
                return
            if path == "/api/save":
                if STATE is None:
                    raise ValueError("No active adventure")
                with LOCK:
                    STATE.model = model
                    slot = str(data.get("slot", "")).strip() or "quicksave"
                    saved = game.save_game(STATE, slot)
                self.send_json({
                    "message": f"Saved as '{saved.stem}' · model {STATE.model}"
                })
                return
            if path == "/api/load":
                slot = str(data.get("slot", "")).strip() or "quicksave"
                with LOCK:
                    STATE = game.load_game(slot)
                    last = STATE.history[-1] if STATE.history else ""
                if last.startswith("DM: "):
                    last = last[4:]
                self.send_json({"state": state_json(STATE), "last": last})
                return
            self.send_json({"error": "Not found"}, 404)
        except SystemExit:
            self.send_json({"error": "Ollama or model is not ready"}, 503)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)


def main() -> None:
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        msg = (
            f"Could not start on http://{HOST}:{PORT}\n\n{exc}\n\n"
            "Another Infinite Storyteller window may already be open, "
            "or something else is using that port.\n\n"
            "Try closing other copies, then open the app again."
        )
        print(msg, file=__import__("sys").stderr)
        try:
            import subprocess
            subprocess.run(
                ["osascript", "-e",
                 f'display alert "Infinite Storyteller" message "{msg}" as critical'],
                check=False,
            )
        except Exception:
            pass
        raise SystemExit(1)

    url = f"http://{HOST}:{PORT}"
    print(f"Infinite Storyteller web UI: {url}")
    print("(Fully offline — only talks to Ollama on this Mac.)")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
