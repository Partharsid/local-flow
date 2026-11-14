"use strict";
/* LocalFlow window: vanilla JS, no build step. State comes from /api/state;
   every control saves immediately through /api/set. */

const ICONS = {
  home: '<path d="M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  book: '<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h11"/>',
  zap: '<path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  chev: '<path d="m9 6 6 6-6 6"/>',
};
const icon = (n) => { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "1.8"); s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round");
  s.innerHTML = ICONS[n] || ""; return s; };

const PAGES = [["home", "Home", "home"], ["dictionary", "Dictionary", "book"], ["snippets", "Snippets", "zap"],
  ["history", "History", "clock"], ["notes", "Meetings & files", "mic"]];
const SETTINGS = ["settings", "Settings", "gear"];

let S = null;            // latest /api/state
let page = "home";
let advOpen = false;

/* ------------------------------------------------------------ helpers */
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}
const $ = (s, r = document) => r.querySelector(s);

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  return r.json();
}
let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.className = "toast"), 1800);
}
const cfg = (path) => path.split(".").reduce((o, k) => (o == null ? o : o[k]), S.config);
async function save(changes, msg = "Saved") {
  const next = await api("/api/set", { changes });
  if (next.error) return toast(next.error, true);
  S = next; toast(msg); live();
}
async function act(name, arg, msg) {
  const r = await api("/api/do", { name, arg });
  if (r.error) toast(r.error, true); else if (msg) toast(msg);
  await refresh();
  if (["start_meeting", "stop_meeting", "pick_file", "unmute_mic", "install_update"].includes(name)) render();
}

/* ------------------------------------------------------------ building blocks */
function row(title, desc, ...ctl) {
  return h("div", { class: "row" }, h("div", null, h("div", { class: "t" }, title), desc && h("div", { class: "d" }, desc)),
    h("div", { class: "ctl" }, ctl));
}
const card = (...rows) => h("div", { class: "card" }, rows);
const section = (t) => h("h2", null, t);

function toggleRow(path, title, desc, after) {
  const input = h("input", { type: "checkbox" });
  input.checked = !!cfg(path);
  input.addEventListener("change", async () => { await save({ [path]: input.checked }); if (after) after(input.checked); });
  return row(title, desc, h("label", { class: "sw" }, input, h("span")));
}
function selectRow(title, desc, options, current, onPick, width) {
  const sel = h("select", { style: width ? `min-width:${width}px` : null },
    options.map(([v, label]) => h("option", { value: String(v ?? ""), selected: String(v ?? "") === String(current ?? "") }, label)));
  sel.addEventListener("change", () => onPick(options.find(([v]) => String(v ?? "") === sel.value)[0]));
  return row(title, desc, sel);
}
function keyRow(key, title, desc) {
  const btn = h("button", { class: "key-btn" }, S.hotkeys[key]);
  btn.addEventListener("click", async () => {
    btn.textContent = "Press keys…"; btn.classList.add("listening");
    const r = await api("/api/hotkey", { key });
    btn.classList.remove("listening");
    if (r.error) toast(r.error, true);
    else if (r.spec) toast("Shortcut set to " + r.label);
    await refresh(); btn.textContent = S.hotkeys[key];
  });
  return row(title, desc, btn);
}
function pairsEditor(path, title, desc, leftPh, rightPh) {
  const box = h("div");
  const data = Object.entries(cfg(path) || {});
  let rows = data.length ? data : [];
  const persist = async () => {
    const o = {};
    for (const r of box.querySelectorAll(".pair")) {
      const [a, b] = r.querySelectorAll("input");
      if (a.value.trim()) o[a.value.trim()] = b.value.replace(/\\n/g, "\n");
    }
    await save({ [path]: o });
  };
  const addRow = (k = "", v = "") => {
    const a = h("input", { type: "text", placeholder: leftPh, value: k });
    const b = h("input", { type: "text", placeholder: rightPh, value: v.replace(/\n/g, "\\n") });
    const x = h("button", { class: "x", title: "Remove", onclick: async () => { r.remove(); await persist(); } }, "✕");
    const r = h("div", { class: "pair" }, a, h("span", { class: "arrow" }, "→"), b, x);
    for (const i of [a, b]) i.addEventListener("change", persist);
    box.append(r); return r;
  };
  rows.forEach(([k, v]) => addRow(k, v));
  const foot = h("div", { class: "pairs-foot" }, h("button", { class: "btn soft sm", onclick: () => addRow().querySelector("input").focus() }, "+ Add"));
  return [h("h2", null, title), card(desc && h("div", { class: "note", style: "padding-top:16px" }, desc), box, foot)];
}
function lead(t, d) { return [h("h1", null, t), h("p", { class: "lead" }, d)]; }

/* ------------------------------------------------------------ pages */
function homePage() {
  const s = S.stats, words = Math.round(s.words || 0), mins = (s.audio_seconds || 0) / 60;
  const saved = Math.max(0, words / 40 - mins), wpm = mins ? Math.round(words / mins) : 0;
  const bars = Array.from({ length: 18 }, (_, i) => h("i", { style: `animation-delay:${(i * 0.07).toFixed(2)}s` }));
  const out = [];
  if (S.status.update) out.push(h("div", { class: "alert update" }, h("div", null, h("b", null, `LocalFlow ${S.status.update.version} is ready. `), "A new version is available."),
    h("button", { class: "btn sm", onclick: () => act("install_update") }, "Install update")));
  if (S.status.mic_muted) out.push(h("div", { class: "alert" }, h("div", null, h("b", null, "Your microphone is muted in Windows. "), "Nothing can be heard until it's unmuted."),
    h("button", { class: "btn sm", onclick: () => act("unmute_mic") }, "Unmute")));
  out.push(...lead("Ready when you are.", "Dictate into any app. Everything is processed on this PC."));
  out.push(h("div", { class: "card hero" },
    h("div", { class: "hero-t" }, "Hold, speak, let go."),
    h("div", { class: "keys" }, h("span", { class: "kbd", "data-live": "hk-hold" }, S.hotkeys.hotkey_hold), h("div", { class: "wave" }, bars)),
    h("div", { class: "hero-d" }, "Your words appear wherever you're typing, cleaned up. Press ", h("b", { "data-live": "hk-toggle" }, S.hotkeys.hotkey_toggle),
      " for hands-free, or Esc to cancel.")));
  out.push(h("div", { class: "stats" },
    h("div", { class: "stat" }, h("b", null, words.toLocaleString()), h("span", null, "words dictated")),
    h("div", { class: "stat" }, h("b", null, Math.round(saved).toLocaleString() + " min"), h("span", null, "saved vs typing")),
    h("div", { class: "stat" }, h("b", null, wpm.toLocaleString()), h("span", null, "words per minute"))));
  const recent = h("div", { class: "card" }, h("div", { class: "empty" }, "Loading…"));
  out.push(section("Recent"), recent);
  api("/api/history").then((r) => {
    recent.replaceChildren();
    if (!r.items.length) return recent.append(h("div", { class: "empty" }, "Your dictations will appear here."));
    r.items.slice(0, 4).forEach((e) => recent.append(entry(e)));
  });
  return out;
}

function entry(e) {
  const d = new Date(e.ts * 1000);
  return h("div", { class: "entry" },
    h("div", { class: "meta" }, h("b", null, d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })), e.app || "unknown"),
    h("div", { class: "txt" }, e.text),
    h("button", { class: "btn soft sm copy", onclick: async () => { try { await navigator.clipboard.writeText(e.text); toast("Copied"); } catch { toast("Couldn't copy", true); } } }, "Copy"));
}

function dictionaryPage() {
  const out = lead("Dictionary", "Names and terms to spell exactly. Near-misses are corrected automatically.");
  const chips = h("div", { class: "chips" });
  const words = () => cfg("dictionary") || [];
  const render = () => {
    chips.replaceChildren();
    if (!words().length) chips.append(h("div", { class: "small" }, "No words yet. Add names, brands and jargon."));
    words().forEach((w) => chips.append(h("span", { class: "chip" }, w,
      h("button", { title: "Remove", onclick: async () => { await save({ dictionary: words().filter((x) => x !== w) }, "Removed"); render(); } }, "×"))));
  };
  const input = h("input", { type: "text", placeholder: "Add a word, e.g. Coolify" });
  const add = async () => { const w = input.value.trim(); if (w && !words().includes(w)) { await save({ dictionary: [...words(), w] }, "Added " + w); render(); } input.value = ""; input.focus(); };
  input.addEventListener("keydown", (e) => e.key === "Enter" && add());
  out.push(card(h("div", { class: "add" }, input, h("button", { class: "btn", onclick: add }, "Add")), chips));
  render();
  out.push(...pairsEditor("replacements", "Replacements", "Always rewrite a phrase that gets misheard.", "Heard, e.g. qualify", "Write, e.g. Coolify"));
  return out;
}

function snippetsPage() {
  return [...lead("Snippets", "Say a short trigger to insert longer text. Use \\n for a new line."),
    ...pairsEditor("snippets", "Your snippets", null, "Say, e.g. my email", "Insert, e.g. me@example.com")].filter(Boolean);
}

function historyPage() {
  const out = lead("History", "Everything you've dictated, stored only on this PC.");
  const list = h("div");
  const search = h("input", { type: "search", placeholder: "Search your dictations…" });
  const clear = h("button", { class: "btn danger sm", onclick: async () => { if (confirm("Delete all saved dictations from this PC?")) { await act("history_clear", null, "History cleared"); load(); } } }, "Clear all");
  out.push(h("div", { class: "search" }, search), list);
  const load = async () => {
    const r = await api("/api/history?q=" + encodeURIComponent(search.value));
    list.replaceChildren();
    if (!r.enabled) return list.append(card(h("div", { class: "empty" }, "History is off.")));
    if (!r.items.length) return list.append(card(h("div", { class: "empty" }, search.value ? "No matches." : "Nothing yet. Hold your shortcut and speak.")));
    const groups = {};
    r.items.forEach((e) => { const k = new Date(e.ts * 1000).toDateString(); (groups[k] ||= []).push(e); });
    const today = new Date().toDateString(), yest = new Date(Date.now() - 864e5).toDateString();
    for (const [k, items] of Object.entries(groups)) {
      list.append(h("div", { class: "day" }, k === today ? "Today" : k === yest ? "Yesterday" : k), card(items.map(entry)));
    }
    list.append(h("div", { style: "margin-top:22px" }, clear));
  };
  let tm; search.addEventListener("input", () => { clearTimeout(tm); tm = setTimeout(load, 160); });
  load();
  return out;
}

function notesPage() {
  const out = lead("Meetings & files", "Turn calls and recordings into text, privately on this PC.");
  out.push(section("Transcribe a file"),
    card(row("Choose a recording", "Audio or video becomes a .txt transcript and .srt subtitles next to the file.",
      h("button", { class: "btn", disabled: S.status.job, onclick: () => act("pick_file", null, "Transcribing…") }, S.status.job ? "Working…" : "Choose file"))));
  out.push(section("Meeting notes"),
    card(row(S.status.meeting ? "Recording…" : "Start a meeting", "Records your microphone and your computer's audio, then writes a transcript, summary and action items. Let others know you're taking notes.",
        S.status.meeting ? h("button", { class: "btn danger", onclick: () => act("stop_meeting", null, "Making notes…") }, "Stop and make notes")
          : h("button", { class: "btn", onclick: () => act("start_meeting", null, "Recording") }, "Start recording")),
      toggleRow("meetings_keep_audio", "Keep the audio recording", "Otherwise only the notes are kept."),
      row("Notes folder", S.notes_folder, h("button", { class: "btn soft sm", onclick: () => act("open_notes") }, "Open"))));
  const learned = [...(cfg("learned_words") || [])].reverse().slice(0, 12);
  out.push(section("Learned from your corrections"),
    card(learned.length ? learned.map((l) => row(`“${l.from}” → ${l.to}`, null)) : h("div", { class: "empty" }, "Nothing learned yet.")));
  return out;
}

function settingsPage() {
  const o = S.options;
  const mic = S.devices;
  const out = lead("Settings", "Everything saves automatically.");
  out.push(section("Dictation"), card(
    keyRow("hotkey_hold", "Shortcut", "Hold to talk. Click, then press the keys you want."),
    selectRow("Language", "Choose your language for the best accuracy.", o.languages.map(([n, c]) => [c, n]), cfg("whisper.language"),
      (v) => save({ "whisper.language": v })),
    selectRow("Microphone", null, mic.map((d) => [d.id, d.name]), cfg("input_device"), (v) => save({ input_device: v }), 220),
    toggleRow("mini_bar", "Show the dictation bar", "A small bar at the bottom of the screen. Click it to dictate."),
    toggleRow("sounds", "Sounds", "Soft start and stop sounds."),
    row("Start with Windows", null, (() => { const i = h("input", { type: "checkbox" }); i.checked = S.autostart;
      i.addEventListener("change", async () => { await act("toggle_autostart", null, "Saved"); }); return h("label", { class: "sw" }, i, h("span")); })())));

  const adv = h("div", { class: "adv", style: advOpen ? "" : "display:none" });
  const tog = h("button", { class: "adv-toggle" + (advOpen ? " open" : ""), onclick: () => { advOpen = !advOpen; tog.classList.toggle("open", advOpen); adv.style.display = advOpen ? "" : "none"; } },
    icon("chev"), "Advanced settings");
  out.push(tog, adv);

  adv.append(section("More shortcuts"), card(
    keyRow("hotkey_toggle", "Hands-free", "Press to start, press again to stop."),
    keyRow("hotkey_edit", "Edit by voice", "Select text, hold, and say what to change."),
    keyRow("hotkey_paste_last", "Paste last transcript", "Re-insert your last dictation.")));

  const backend = cfg("cleanup.backend") || "builtin";
  const engines = [["cloud", "Cloud · OpenRouter, Gemini, Groq"], ["builtin", "Built-in · private, offline"], ["ollama", "Ollama · your own models"]];
  const ai = [toggleRow("cleanup.enabled", "AI cleanup", "Removes fillers, applies self-corrections, fixes punctuation, formats lists."),
    selectRow("Engine", "Cloud is best at lists and paragraphs and uses no GPU. Built-in runs fully offline.", engines, backend,
      async (v) => { await save({ "cleanup.backend": v }, "Switching engine…"); render(); }, 250)];
  if (backend === "ollama") {
    const sel = h("select", null, h("option", null, cfg("cleanup.model")));
    api("/api/ollama").then((r) => { if (r.models.length) { sel.replaceChildren(...r.models.map((m) => h("option", { selected: m === cfg("cleanup.model") }, m))); } });
    sel.addEventListener("change", () => save({ "cleanup.model": sel.value }));
    ai.push(row("Ollama model", "Any Ollama chat model.", sel));
  }
  ai.push(toggleRow("cleanup.fast_model", "Fast dictation model", "LocalFlow's own small model for dictation: about 3x faster."),
    toggleRow("cleanup.prompt_expert", "Expert prompts", "Prompt mode also adds \"ask me if anything is unclear\" and \"check your work\"."),
    toggleRow("cleanup.save_training_data", "Save dictations to improve the model", `Kept only on this PC. ${S.training.dictations} saved, ${S.training.corrections} corrected.`));
  adv.append(section("AI cleanup"), card(ai));

  // cloud key
  const key = h("input", { type: "password", placeholder: S.cloud.has_key ? "Key saved · paste a new one to replace" : "Paste an OpenRouter, Gemini or Groq key" });
  const stat = h("div", { class: "inline-status", "data-live": "cloud" });
  const go = h("button", { class: "btn", onclick: async () => { await api("/api/cloud", { key: key.value }); key.value = ""; refresh(); } }, S.cloud.model ? "Re-test" : "Connect");
  adv.append(section("Cloud AI"), card(
    toggleRow("cleanup.cloud_free_only", "OpenRouter: free models only", "Free use is 50 requests a day. Turn off to pick the best paid model."),
    h("div", { class: "pad" }, h("div", { class: "add", style: "padding:0" }, key, go,
      S.cloud.has_key && h("button", { class: "btn soft", onclick: async () => { await api("/api/cloud", { remove: true }); toast("Key removed"); await refresh(); render(); } }, "Remove"))),
    stat, h("div", { class: "note" }, "Your key is kept in Windows Credential Manager. With the cloud engine, each transcript and the app's name are sent to the provider; audio and screen text never leave this PC.")));

  adv.append(section("Speech recognition"), card(selectRow("Whisper model", "Runs on this PC. Changing the model downloads it once.",
    o.whisper_models, cfg("whisper.model"), (v) => save({ "whisper.model": v }, "Switching model…"), 280)));

  adv.append(section("Smart formatting"), card(
    toggleRow("use_context", "Read text around the cursor", "Matches capitals, spacing and names to what you're writing. Stays on this PC."),
    toggleRow("voice_commands", "Voice commands", "Say “new line” or “new paragraph”."),
    toggleRow("remove_fillers", "Remove filler words", "um, uh, erm…"),
    toggleRow("restore_clipboard", "Restore clipboard after pasting", "Your copied content comes back automatically."),
    toggleRow("live_preview", "Live preview", "Show your words in the bar while you speak (NVIDIA GPU only)."),
    toggleRow("stream_transcription", "Transcribe while I speak", "Long dictations appear soon after you stop."),
    toggleRow("learn_from_corrections", "Learn from my corrections", "Fix a misheard name right after dictating and it joins your Dictionary."),
    toggleRow("keep_mic_warm", "Keep microphone ready", "Never clips your first word, but keeps the mic in use.")));

  const styleRows = o.app_kinds.map(([kind, title, hint]) => selectRow(title, hint || null, o.styles,
    (cfg("app_styles") || {})[kind] || "neutral", (v) => save({ [`app_styles.${kind}`]: v }), 210));
  adv.append(section("Writing style per app"), card(styleRows));
  adv.append(...pairsEditor("app_style_overrides", "Per-app overrides", "Give one app its own style: casual, neutral, formal, technical, notes, prompt or verbatim.", "App, e.g. slack.exe", "Style, e.g. formal"));

  adv.append(section("About"), card(
    row(`LocalFlow ${S.version}`, "Private voice dictation that runs entirely on your PC.",
      h("button", { class: "btn soft sm", onclick: () => act("check_updates", null, "Checking… you'll get a notification") }, "Check for updates")),
    toggleRow("update_check", "Check for updates automatically", "Once a day; you choose when to install."),
    row("Files", null, h("button", { class: "btn soft sm", onclick: () => act("open_folder", "logs") }, "Logs"),
      h("button", { class: "btn soft sm", onclick: () => act("open_folder", "data") }, "Data folder"))));
  return out;
}

/* ------------------------------------------------------------ shell */
function buildNav() {
  const nav = $("#nav"); nav.replaceChildren();
  for (const [key, label, ic] of PAGES) nav.append(h("button", { class: "nav-item", "data-page": key, onclick: () => go(key) }, icon(ic), label));
  nav.append(h("div", { class: "nav-gap" }));
  nav.append(h("button", { class: "nav-item", "data-page": SETTINGS[0], onclick: () => go("settings") }, icon(SETTINGS[2]), SETTINGS[1]));
}
const BUILDERS = { home: homePage, dictionary: dictionaryPage, snippets: snippetsPage, history: historyPage, notes: notesPage, settings: settingsPage };
function render() {
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.toggle("on", n.dataset.page === page));
  const el = $("#page"); const fresh = el.cloneNode(false); fresh.style.animation = "none"; void fresh.offsetWidth; fresh.style.animation = "";
  fresh.append(...(BUILDERS[page] || homePage)());
  el.replaceWith(fresh); document.querySelector(".main").scrollTop = 0; live();
}
function go(p) { page = p; history.replaceState(null, "", "#" + p); render(); }

function live() {
  if (!S) return;
  const st = S.status, pill = $("#statusPill"), tx = $("#statusText");
  let cls = "ready", text = "Ready";
  if (st.speech === "error") { cls = "error"; text = "Speech model failed"; }
  else if (st.speech === "loading") { cls = "loading"; text = "Getting ready…"; }
  else if (st.download) { cls = "loading"; text = st.download; }
  else if (!st.ai_on) text = "Ready · AI cleanup off";
  pill.className = "status-pill " + cls; tx.textContent = text;
  $("#ver").textContent = "Version " + S.version + (st.device ? " · " + st.device : "");
  const hold = $('[data-live="hk-hold"]'); if (hold) hold.textContent = S.hotkeys.hotkey_hold;
  const tg = $('[data-live="hk-toggle"]'); if (tg) tg.textContent = S.hotkeys.hotkey_toggle;
  const cl = $('[data-live="cloud"]');
  if (cl) { const j = S.cloud.job; cl.textContent = j.text || (S.cloud.model ? `Using ${S.cloud.model} via ${S.cloud.provider}.` : "No key yet. Get a free OpenRouter key at openrouter.ai/keys.");
    cl.className = "inline-status " + (j.state === "ok" ? "ok" : j.state === "error" ? "bad" : ""); }
}
async function refresh() { try { S = await api("/api/state"); live(); } catch { /* app closed */ } }

(async function init() {
  await refresh();
  buildNav();
  page = (location.hash || "#home").slice(1);
  if (!BUILDERS[page]) page = "home";
  render();
  setInterval(async () => { await refresh(); if (page === "notes" && !document.activeElement?.closest?.("input")) { /* keep inputs */ } }, 2500);
})();
