/* ============================================================
   ANSIO voice bridge — LiveKit Web SDK -> standalone render seam
   ------------------------------------------------------------
   Connects the three-column UI to a real LiveKit room:
     - publishes the founder's microphone
     - plays the agent's TTS audio track
     - turns DataChannel events into evidence cards + retrieval HUD
     - relays typed text to the agent (dual-mode interaction)

   Contract sources (do not invent APIs):
     - agent-py/src/agent.py:_publish_moss_context  -> the REAL, shipped
       event: {type:"moss_context", data:{query, matches[], time_taken_ms,
       timestamp}} broadcast via publish_data(reliable=True) (NO topic).
     - .omc/research/ansio-v4/f4-ui-blueprint.md section 4 -> target 9-type events.
     - .omc/research/ansio-execution-v3/04-bridge-design.md A/B.

   Secrets discipline: this file references variable names only
   (LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET / AGENT_NAME)
   through the token server; no values appear here.

   Degradation discipline: every external call is guarded. If the token
   endpoint or room is unavailable, the UI stays usable (typed text still
   echoes locally; the standalone demo remains the fallback).
   ============================================================ */
(function () {
  "use strict";

  var LK = window.LivekitClient; // UMD global from vendor/livekit-client.umd.min.js
  var TOK = window.ANSIO_TOKEN_ENDPOINT || "/token"; // same-origin: token server also serves this page (one port, one SSH tunnel)
  var AGENT_NAME = window.ANSIO_AGENT_NAME || "agent-py"; // must match worker reg name
  var room = null;
  var connected = false;

  function ig() {
    return (window.ANSIO && window.ANSIO.ingest) || null;
  }
  function toast(msg) {
    try {
      console.warn("[ansio-bridge]", msg);
      var el = document.getElementById("ansio-bridge-toast");
      if (!el) {
        el = document.createElement("div");
        el.id = "ansio-bridge-toast";
        el.style.cssText =
          "position:fixed;bottom:14px;left:14px;z-index:2147483647;" +
          "font:12px/1.4 -apple-system,system-ui,sans-serif;color:#f4f1ea;" +
          "background:rgba(107,77,236,.92);padding:8px 13px;border-radius:8px;" +
          "box-shadow:0 4px 18px rgba(0,0,0,.35);max-width:60vw;";
        document.body.appendChild(el);
      }
      el.textContent = msg;
      clearTimeout(el._t);
      el._t = setTimeout(function () {
        if (el && el.parentNode) el.parentNode.removeChild(el);
      }, 4200);
    } catch (e) {}
  }
  function clampMs(v) {
    if (v == null) return null;
    var n = Math.round(Number(v));
    return isFinite(n) ? n : null;
  }

  async function connect() {
    if (connected) return room;
    if (!LK) {
      toast("LiveKit SDK missing - running in static/typed mode.");
      return null;
    }
    var cd;
    try {
      var r = await fetch(TOK, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          room_config: { agents: [{ agent_name: AGENT_NAME }] },
        }),
      });
      if (!r.ok) throw new Error("token " + r.status);
      cd = await r.json();
    } catch (e) {
      toast("Voice unavailable, staying in typed/static mode: " + e.message);
      return null;
    }

    try {
      room = new LK.Room({ adaptiveStream: true, dynacast: true });

      room.on(LK.RoomEvent.TrackSubscribed, function (track) {
        if (track.kind === LK.Track.Kind.Audio) {
          try {
            var el = track.attach();
            el.autoplay = true;
            el.setAttribute("data-ansio-agent-audio", "1");
            document.body.appendChild(el);
          } catch (e) {}
          var i = ig();
          if (i) i.setSpeaking(true);
          if (track.on) {
            track.on("ended", function () {
              var j = ig();
              if (j) j.setSpeaking(false);
            });
          }
        }
      });

      room.on(LK.RoomEvent.TrackUnsubscribed, function (track) {
        if (track.kind === LK.Track.Kind.Audio) {
          var i = ig();
          if (i) i.setSpeaking(false);
          try {
            track.detach().forEach(function (el) {
              if (el.parentNode) el.parentNode.removeChild(el);
            });
          } catch (e) {}
        }
      });

      room.on(LK.RoomEvent.DataReceived, function (payload) {
        var msg;
        try {
          msg = JSON.parse(new TextDecoder().decode(payload));
        } catch (e) {
          return; // dirty packet -> silently drop (degradation discipline)
        }
        if (!msg || typeof msg !== "object") return;
        if (msg.type === "moss_context") return renderMossContext(msg.data || {});
        if (msg.type === "transcript") return renderTranscript(msg);
        if (msg.type && EVIDENCE_TYPES.has(msg.type)) return renderEvidence(msg);
      });

      if (LK.RoomEvent.TranscriptionReceived) {
        room.on(LK.RoomEvent.TranscriptionReceived, function (segs) {
          try {
            (segs || []).forEach(function (s) {
              if (s && s.final && s.text) {
                renderTranscript({ role: "assistant", text: s.text });
              }
            });
          } catch (e) {}
        });
      }

      room.on(LK.RoomEvent.Disconnected, function () {
        connected = false;
        var i = ig();
        if (i) i.setSpeaking(false);
      });

      await room.connect(cd.serverUrl, cd.participantToken);
      try {
        await room.localParticipant.setMicrophoneEnabled(true);
      } catch (e) {
        toast("Microphone blocked - typed mode still works.");
      }
      connected = true;
      window.ANSIO_BRIDGE = window.ANSIO_BRIDGE || {};
      window.ANSIO_BRIDGE.sendText = sendText;
      toast("Connected - speak or type to ANSIO");
      return room;
    } catch (e) {
      toast("Room connect failed, staying in typed/static mode: " + e.message);
      connected = false;
      return null;
    }
  }

  function renderMossContext(d) {
    var i = ig();
    if (!i) return;
    var matches = Array.isArray(d.matches) ? d.matches : [];
    var ms = clampMs(d.time_taken_ms);
    var msLabel = ms != null ? ms + "ms" : "<10ms";
    var query = (d.query || "").slice(0, 48);

    var steps = [{ t: 'Querying Moss - "' + query + '"' }];
    matches.slice(0, 4).forEach(function (m) {
      steps.push({
        t: (m.text || "").replace(/\s+/g, " ").slice(0, 72),
        src: m.metadata && m.metadata.source,
        res: false,
      });
    });
    steps.push({ t: matches.length + " matches - " + msLabel, res: true });
    i.setRetrieveChain("Retrieving from Moss", steps);

    var key = "moss_" + Date.now();
    i.registerCard(key, {
      step: "..",
      id: key,
      title: "Moss Retrieval",
      sub: matches.length + " hits - " + msLabel,
      kind: "content",
      items: matches.slice(0, 5).map(function (m) {
        return {
          title: (m.text || "").replace(/\s+/g, " ").slice(0, 90),
          views: m.score,
          source: m.metadata && m.metadata.source,
        };
      }),
    });
    setTimeout(function () {
      var j = ig();
      if (j) {
        j.pushCard(key);
        j.setRetrieveChain(null);
      }
    }, 700);
  }

  var EVIDENCE_TYPES = new Set([
    "competitor_landscape",
    "content_hits",
    "playbook_hit",
    "kol_profile",
    "similar_creators",
    "alpha_ranking",
    "bundle",
    "content_strategy",
    "roi_forecast",
  ]);
  var KIND = {
    competitor_landscape: "competitors",
    content_hits: "content",
    playbook_hit: "content",
    kol_profile: "creators",
    similar_creators: "creators",
    alpha_ranking: "leaderboard",
    bundle: "recommend",
    content_strategy: "channels",
    roi_forecast: "roi",
  };
  function renderEvidence(evt) {
    if (!evt.type || !EVIDENCE_TYPES.has(evt.type) || !Array.isArray(evt.items)) {
      return; // boundary validation: drop malformed events
    }
    var i = ig();
    if (!i) return;
    var ms = clampMs(evt.latency_ms);
    var key = evt.type + "_" + (evt.step || Date.now());
    i.registerCard(
      key,
      {
        step: String(evt.step || ".."),
        id: key,
        title: evt.title || evt.type,
        sub: evt.insight || "",
        kind: KIND[evt.type] || "content",
        items: evt.items,
      },
      [{ t: evt.insight || "Retrieved", src: evt.source, res: true }]
    );
    i.setRetrieveChain(evt.insight || "Retrieving", [
      { t: (evt.index || "moss") + (ms != null ? " - " + ms + "ms" : ""), res: true },
    ]);
    setTimeout(function () {
      var j = ig();
      if (j) {
        j.pushCard(key);
        j.setRetrieveChain(null);
      }
    }, 500);
  }

  function renderTranscript(m) {
    var i = ig();
    if (!i || !m || !m.text) return;
    if (m.role === "user") i.pushUser(m.text);
    else i.pushAI(m.text);
  }

  async function sendText(text) {
    if (!room || !connected) return; // caller already echoed locally
    try {
      await room.localParticipant.sendText(text, { topic: "lk.chat" });
    } catch (e) {
      try {
        await room.localParticipant.publishData(
          new TextEncoder().encode(JSON.stringify({ type: "user_text", text: text })),
          { reliable: true, topic: "user_text" }
        );
      } catch (e2) {}
    }
  }

  window.ANSIO_BRIDGE = window.ANSIO_BRIDGE || {};
  window.ANSIO_BRIDGE.connect = connect;
  window.ANSIO_BRIDGE.sendText = sendText;
})();
