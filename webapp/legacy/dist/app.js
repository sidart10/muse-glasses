// Muse on Ray-Ban Display. Talks to muse-bridge on Sid's Mac mini:
//   POST /api/say {text}  → queued for Muse (returns at once)
//   GET  /api/feed?after  → long-poll of status / reply / done / error events
// Input: Meta AI voice via WebMCP (talk_to_muse), or dictation into the field.
// Output: Muse's reply on the lens, read aloud with speechSynthesis.
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const ui = { dot: $("dot"), status: $("status"), you: $("you"), muse: $("muse"),
               form: $("composer"), input: $("input"), hint: $("hint"), steps: $("steps"),
               approval: $("approval"), approvalTitle: $("approval-title"),
               approvalDetails: $("approval-details") };

  // --- live activity timeline (last 3 steps of the current request)
  const MAX_STEPS = 3;
  function resetSteps() { ui.steps.replaceChildren(); }
  function addStep(text) {
    const last = ui.steps.lastElementChild;
    if (last && last.textContent === text) return;
    if (last) last.classList.remove("now");
    const li = document.createElement("li");
    li.textContent = text; li.className = "now";
    ui.steps.append(li);
    while (ui.steps.children.length > MAX_STEPS) ui.steps.firstElementChild.remove();
  }
  function finishSteps() { const last = ui.steps.lastElementChild; if (last) last.classList.remove("now"); }

  // --- approvals: shown and spoken; decided on the phone (no device API for it)
  let spokenApprovals = new Set();
  function showApprovals(items) {
    if (!items.length) { ui.approval.hidden = true; return; }
    const first = items[0];
    ui.approvalTitle.textContent = items.length > 1 ? `${first.action} (+${items.length - 1} more)` : first.action;
    ui.approvalDetails.replaceChildren(...first.details.map((d) => {
      const li = document.createElement("li"); li.textContent = d; return li; }));
    ui.approval.hidden = false;
    if (!spokenApprovals.has(first.id)) {
      spokenApprovals.add(first.id);
      speak(`Muse needs your approval to ${first.action}. Approve it in the Muse app on your phone.`);
    }
  }

  // --- token: arrives once in the install link's #k=, then lives in localStorage
  const TOKEN_KEY = "museBridgeToken";
  const fromHash = new URLSearchParams(location.hash.slice(1)).get("k");
  if (fromHash) {
    localStorage.setItem(TOKEN_KEY, fromHash);
    history.replaceState(null, "", location.pathname);
  }
  const token = () => localStorage.getItem(TOKEN_KEY) || "";

  async function api(path, options = {}) {
    const resp = await fetch(path, {
      ...options,
      headers: { "Authorization": `Bearer ${token()}`, "Content-Type": "application/json",
                 ...(options.headers || {}) },
    });
    if (resp.status === 401) throw new Error("This glasses link is no longer valid. Install it again.");
    if (resp.status === 503) throw new Error("The bridge is switched off.");
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(body.error || `HTTP ${resp.status}`);
    return body;
  }

  // --- speech
  function speak(text) {
    if (!("speechSynthesis" in window) || !text) return;
    speechSynthesis.cancel();
    speechSynthesis.speak(new SpeechSynthesisUtterance(text));
  }

  // --- audio probe: can this device play real audio (needed for Muse's own voice)?
  // Plays a short two-note chime before each spoken reply and reports the result.
  let audioReport = "";
  async function chime() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) { audioReport = "audio: no AudioContext"; return; }
      const ctx = chime.ctx || (chime.ctx = new Ctx());
      if (ctx.state === "suspended") await ctx.resume();
      [660, 880].forEach((freq, i) => {
        const osc = ctx.createOscillator(), gain = ctx.createGain();
        osc.frequency.value = freq; gain.gain.value = 0.15;
        osc.connect(gain).connect(ctx.destination);
        osc.start(ctx.currentTime + i * 0.15); osc.stop(ctx.currentTime + i * 0.15 + 0.12);
      });
      audioReport = `audio: ${ctx.state}`;
    } catch (err) { audioReport = `audio: ${err.name}`; }
  }

  // --- view
  let pending = null;   // request id we are waiting on
  function show(state) {
    if (state.you !== undefined) ui.you.textContent = state.you ? `You: ${state.you}` : "";
    if (state.muse !== undefined) {
      ui.muse.textContent = state.muse;
      ui.muse.scrollTop = 0;
      ui.muse.classList.toggle("error", !!state.error);
    }
    if (state.status !== undefined) ui.status.textContent = state.status;
  }
  function setConnected(connected) {
    ui.dot.className = "dot " + (pending ? "busy" : connected ? "on" : "");
    if (!pending) ui.status.textContent = connected ? "" : "Muse offline";
  }

  // --- sending
  async function say(text) {
    text = (text || "").trim();
    if (!text) return "Nothing to send.";
    const { request_id } = await api("/api/say", { method: "POST", body: JSON.stringify({ text }) });
    pending = request_id;
    show({ you: text, muse: "", status: "sending…" });
    resetSteps();
    setConnected(true);
    return request_id;
  }

  ui.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = ui.input.value;
    ui.input.value = "";
    try { await say(text); } catch (err) { show({ muse: err.message, error: true, status: "" }); }
  });
  // Dictation from the glasses composer lands as input/change on the field.
  ui.input.addEventListener("change", () => ui.form.requestSubmit());

  // --- feed: one long-poll loop
  let after = 0;
  async function pollForever() {
    // Skip history from before this app opened.
    try { const first = await api("/api/feed?after=0&wait=0");
          after = first.events.length ? first.events[first.events.length - 1].seq : 0;
          const lastApprovals = first.events.filter((e) => e.type === "approvals").pop();
          if (lastApprovals) showApprovals(lastApprovals.items || []);
          setConnected(first.connected); } catch (err) { show({ muse: err.message, error: true }); }
    for (;;) {
      try {
        const { connected, events } = await api(`/api/feed?after=${after}`);
        if (pending && !connected) show({ status: "Muse reconnecting…" });
        for (const event of events) { after = event.seq; handle(event); }
        setConnected(connected);
      } catch (err) {
        show({ status: "reconnecting…" });
        await new Promise((r) => setTimeout(r, 3000));
      }
    }
  }

  function handle(event) {
    if (event.type === "approvals") { showApprovals(event.items || []); return; }
    if (event.type === "sent" && event.request_id !== pending) {
      pending = event.request_id;          // sent by voice (WebMCP) or another client
      show({ you: event.text, muse: "", status: "sending…" });
      resetSteps();
    }
    if (event.request_id !== pending) return;
    if (event.type === "status") show({ status: event.text });
    else if (event.type === "activity") { addStep(event.text); show({ status: "working…" }); }
    else if (event.type === "reply") {
      show({ muse: event.text, status: event.done ? "" : "answering…" });
      if (event.done) {
        chime().then(() => { ui.hint.textContent = audioReport; setTimeout(() => speak(event.text), 400); });
      }
    } else if (event.type === "error") {
      pending = null;
      finishSteps();
      show({ muse: event.text, error: true, status: "" });
      speak(event.text);
    } else if (event.type === "done") {
      pending = null;
      finishSteps();
      show({ status: "" });
    }
  }

  // --- input: arrows move focus between the reply and the field (no pointer on glasses)
  document.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" && document.activeElement !== ui.input) { ui.input.focus(); event.preventDefault(); }
    else if (event.key === "ArrowUp" && document.activeElement === ui.input) { ui.muse.focus(); event.preventDefault(); }
    else if (event.key === "Enter" && document.activeElement === ui.muse) { speak(ui.muse.textContent); }
    // Long answers ("tell me more"): swipe left/right pages through the reply.
    else if ((event.key === "ArrowRight" || event.key === "ArrowLeft") && document.activeElement === ui.muse) {
      ui.muse.scrollBy({ top: (event.key === "ArrowRight" ? 1 : -1) * (ui.muse.clientHeight - 36), behavior: "smooth" });
      event.preventDefault();
    }
  });

  // --- WebMCP: let Meta AI hand Sid's spoken words to Muse (ticket 05)
  function registerVoiceTools() {
    if (!document.modelContext || !document.modelContext.registerTool) {
      ui.hint.textContent = "Voice tools unavailable here: dictate instead.";
      return;
    }
    const controller = new AbortController();
    Promise.resolve().then(() => document.modelContext.registerTool({
      name: "talk_to_muse",
      description: "Send the user's message to their personal agent Muse, word for word. " +
        "Use whenever the user says to tell, ask or message Muse. Pass exactly what they said " +
        "after 'tell Muse' or 'ask Muse', without rephrasing or summarizing. Muse's answer " +
        "appears on the display and is read aloud separately.",
      inputSchema: {
        type: "object",
        properties: { message: { type: "string", description: "The user's words for Muse, verbatim." } },
        required: ["message"],
      },
      async execute(input) {
        try { await say(input && input.message); }
        catch (err) { throw new Error(`Couldn't reach Muse: ${err.message}`); }
        return { content: [{ type: "text", text: "Sent to Muse. The answer will show on the display." }] };
      },
    }, { signal: controller.signal }))
      .then(() => { ui.hint.textContent = 'Say "Hey Meta, tell Muse…"'; })
      .catch((err) => { ui.hint.textContent = `Voice tool not registered: ${err.message}`; });
    window.addEventListener("pagehide", () => controller.abort(), { once: true });
  }

  if (!token()) {
    show({ muse: "Open this app from its install link to connect it to your Mac.", error: true });
  } else {
    registerVoiceTools();
    pollForever();
  }
  ui.muse.focus();
})();
