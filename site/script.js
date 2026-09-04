// ContextOS marketing site — no build step, no framework, no dependency.
// Behaviors: reveal-on-scroll (staggered), scroll-progress bar, cursor
// spotlight, 3D card tilt, active-nav highlighting, cycling eyebrow text.
// Everything degrades gracefully: prefers-reduced-motion disables the
// purely decorative bits, and pointer:coarse (touch) skips cursor-only fx.

(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var finePointer = window.matchMedia("(pointer: fine)").matches;

  /* ---------- reveal-on-scroll (staggered) ---------- */

  function animateCount(el) {
    var target = parseFloat(el.getAttribute("data-count"));
    if (Number.isNaN(target)) return;
    if (reduceMotion) {
      el.textContent = "+" + target.toFixed(1) + "%";
      return;
    }
    var start = performance.now();
    var duration = 1200;
    function step(now) {
      var t = Math.min(1, (now - start) / duration);
      var eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
      el.textContent = "+" + (target * eased).toFixed(1) + "%";
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function animateBar(el) {
    var pct = el.getAttribute("data-pct");
    if (!pct) return;
    requestAnimationFrame(function () {
      el.style.width = pct + "%";
    });
  }

  // stagger: siblings sharing a parent reveal together with a small
  // per-index delay, so a grid of cards cascades in instead of popping
  // as one flat block.
  document.querySelectorAll(
    ".grid-3, .grid-2, .bento, .fw-strip, .headline-cards, .lever-list, .mini-cards"
  ).forEach(function (group) {
    var i = 0;
    Array.prototype.forEach.call(group.children, function (child) {
      if (child.classList.contains("reveal")) {
        child.style.setProperty("--stagger", i * 70 + "ms");
        i++;
      }
    });
  });

  var seen = new WeakSet();

  var revealObserver = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting || seen.has(entry.target)) return;
        seen.add(entry.target);
        entry.target.classList.add("in");
        entry.target.querySelectorAll("[data-count]").forEach(animateCount);
        entry.target.querySelectorAll("[data-pct]").forEach(animateBar);
      });
    },
    { threshold: 0.2 }
  );

  document.querySelectorAll(".reveal").forEach(function (el) {
    revealObserver.observe(el);
  });

  window.addEventListener("load", function () {
    document.querySelectorAll(".reveal").forEach(function (el) {
      var rect = el.getBoundingClientRect();
      if (rect.top < window.innerHeight && rect.bottom > 0 && !seen.has(el)) {
        revealObserver.unobserve(el);
        seen.add(el);
        el.classList.add("in");
        el.querySelectorAll("[data-count]").forEach(animateCount);
        el.querySelectorAll("[data-pct]").forEach(animateBar);
      }
    });
  });

  /* ---------- smooth anchor scroll (nav-height aware) ---------- */

  var NAV_HEIGHT = 88;
  document.querySelectorAll('a[href^="#"]').forEach(function (link) {
    link.addEventListener("click", function (e) {
      var id = link.getAttribute("href").slice(1);
      if (!id) return;
      var target = document.getElementById(id);
      if (!target) return;
      e.preventDefault();
      var y = target.getBoundingClientRect().top + window.pageYOffset - NAV_HEIGHT;
      window.scrollTo({ top: y, behavior: reduceMotion ? "auto" : "smooth" });
    });
  });

  /* ---------- scroll progress bar ---------- */

  var progressBar = document.getElementById("scrollProgress");
  function updateProgress() {
    var doc = document.documentElement;
    var scrollable = doc.scrollHeight - doc.clientHeight;
    var pct = scrollable > 0 ? (doc.scrollTop / scrollable) * 100 : 0;
    if (progressBar) progressBar.style.width = pct + "%";
  }

  /* ---------- active-nav highlighting ---------- */

  var navLinks = Array.prototype.slice.call(document.querySelectorAll("nav.top ul a"));
  var sections = navLinks
    .map(function (link) {
      var id = link.getAttribute("href").slice(1);
      return document.getElementById(id);
    })
    .filter(Boolean);

  var sectionObserver = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        var link = navLinks.find(function (l) {
          return l.getAttribute("href") === "#" + entry.target.id;
        });
        if (!link) return;
        if (entry.isIntersecting) {
          navLinks.forEach(function (l) { l.classList.remove("active"); });
          link.classList.add("active");
        }
      });
    },
    { rootMargin: "-45% 0px -50% 0px", threshold: 0 }
  );

  sections.forEach(function (s) { sectionObserver.observe(s); });

  /* ---------- single rAF-throttled scroll handler ---------- */

  var ticking = false;
  window.addEventListener(
    "scroll",
    function () {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        updateProgress();
        ticking = false;
      });
    },
    { passive: true }
  );
  updateProgress();

  /* ---------- cursor spotlight (desktop only) ---------- */

  var glow = document.getElementById("cursorGlow");
  if (glow && finePointer && !reduceMotion) {
    var gx = 0, gy = 0, cx = 0, cy = 0;
    document.addEventListener("mousemove", function (e) {
      gx = e.clientX;
      gy = e.clientY;
      glow.classList.add("active");
    });
    document.addEventListener("mouseleave", function () {
      glow.classList.remove("active");
    });
    (function loop() {
      cx += (gx - cx) * 0.12;
      cy += (gy - cy) * 0.12;
      glow.style.transform = "translate(" + cx + "px, " + cy + "px) translate(-50%, -50%)";
      requestAnimationFrame(loop);
    })();
  }

  /* ---------- 3D card tilt (desktop only) ---------- */

  // One delegated listener instead of three per card: the page has 24+ .tilt
  // elements, and transform writes are batched into a single rAF so a fast
  // pointer sweep cannot queue more style work than the compositor can flush.
  if (finePointer && !reduceMotion) {
    var tiltCard = null;
    var tiltRect = null;
    var tiltX = 0;
    var tiltY = 0;
    var tiltQueued = false;

    function resetTilt(card) {
      card.style.transform = "perspective(700px) rotateX(0) rotateY(0) translateY(0)";
    }

    function flushTilt() {
      tiltQueued = false;
      if (!tiltCard || !tiltRect) return;
      var px = (tiltX - tiltRect.left) / tiltRect.width; // 0..1
      var py = (tiltY - tiltRect.top) / tiltRect.height;
      var rotY = (px - 0.5) * 10; // deg
      var rotX = (0.5 - py) * 10;
      tiltCard.style.transform =
        "perspective(700px) rotateX(" + rotX + "deg) rotateY(" + rotY + "deg) translateY(-4px)";
    }

    document.addEventListener(
      "mousemove",
      function (e) {
        var card = e.target.closest ? e.target.closest(".tilt") : null;
        if (card !== tiltCard) {
          if (tiltCard) resetTilt(tiltCard);
          tiltCard = card;
          tiltRect = card ? card.getBoundingClientRect() : null;
        }
        if (!tiltCard) return;
        tiltX = e.clientX;
        tiltY = e.clientY;
        if (!tiltQueued) {
          tiltQueued = true;
          requestAnimationFrame(flushTilt);
        }
      },
      { passive: true }
    );

    // Pointer leaving the window never fires a further mousemove, so the last
    // hovered card would otherwise stay stuck mid-tilt.
    document.addEventListener("mouseleave", function () {
      if (tiltCard) resetTilt(tiltCard);
      tiltCard = null;
      tiltRect = null;
    });

    // Cached rects go stale the moment the page moves under the pointer.
    window.addEventListener(
      "scroll",
      function () {
        if (tiltCard) tiltRect = tiltCard.getBoundingClientRect();
      },
      { passive: true }
    );
  }

  /* ---------- cycling eyebrow text ---------- */

  var eyebrowEl = document.getElementById("eyebrowCycle");
  if (eyebrowEl && !reduceMotion) {
    var messages = [
      "Live API calls, real tokens, ground-truth verified",
      "+53.2% cost-weighted on LangGraph",
      "+43.4% cost-weighted on CrewAI",
      "+56.7% cost-weighted on OpenAI Agents SDK",
      "+54.8% cost-weighted on AutoGen",
    ];
    var mi = 0;
    setInterval(function () {
      eyebrowEl.classList.add("swap-out");
      setTimeout(function () {
        mi = (mi + 1) % messages.length;
        eyebrowEl.textContent = messages[mi];
        eyebrowEl.classList.remove("swap-out");
      }, 350);
    }, 3200);
  }

  /* ---------- Auditor live monitor ---------- */
  /* Illustrative replay of real captured sessions (see docs/product/
     live-results-2026-08-30.md for the underlying numbers). Finding labels
     describe WHAT was caught, not how — the mechanism is the paid SDK's IP. */

  var monLog = document.getElementById("monLog");
  var monTokens = document.getElementById("monTokens");
  var monGaugeFill = document.getElementById("monGaugeFill");
  var monGaugePct = document.getElementById("monGaugePct");
  var monFindings = document.getElementById("monFindings");
  var monSession = document.getElementById("monSession");
  var GAUGE_CIRCUMFERENCE = 327;

  var monSessions = [
    {
      label: "crewai · ecommerce-order-support",
      turns: [
        { tool: "read_order_history", tokens: 3120 },
        { tool: "search_refund_policy", tokens: 5480 },
        { tool: "update_ticket_status", tokens: 8140 },
        { tool: "compose_customer_reply", tokens: 10380 },
      ],
      savings: 1.7,
      findings: ["Oversized tool menu trimmed", "Duplicate call caught"],
    },
    {
      label: "langgraph · legal-clause-lookup",
      turns: [
        { tool: "search_contract", tokens: 2260 },
        { tool: "read_clause_section", tokens: 4010 },
        { tool: "cross_reference_amendment", tokens: 6870 },
        { tool: "draft_summary", tokens: 8920 },
      ],
      savings: 69.6,
      findings: ["Redundant re-read avoided", "Search stayed a single turn"],
    },
    {
      label: "autogen · it-helpdesk-config",
      turns: [
        { tool: "read_device_config", tokens: 2890 },
        { tool: "check_known_issues", tokens: 5230 },
        { tool: "apply_config_patch", tokens: 7460 },
        { tool: "verify_and_close", tokens: 9310 },
      ],
      savings: 64.3,
      findings: ["Independent edits sent together", "Oversized tool menu trimmed"],
    },
  ];

  function runMonitor(index) {
    if (!monLog) return;
    var session = monSessions[index % monSessions.length];
    monLog.innerHTML = "";
    monFindings.innerHTML = "";
    monTokens.textContent = "0";
    monGaugeFill.style.strokeDashoffset = GAUGE_CIRCUMFERENCE;
    monGaugePct.textContent = "0%";
    if (monSession) monSession.textContent = session.label;

    if (reduceMotion) {
      // Static end-state only, no animation, no auto-advance.
      session.turns.forEach(function (t, i) {
        var line = document.createElement("div");
        line.className = "mon-log-line";
        line.style.opacity = "1";
        line.style.animation = "none";
        line.innerHTML =
          '<span class="mon-turn">turn ' + (i + 1) + "</span> " +
          '<span class="mon-tool">' + t.tool + "</span> " +
          '<span class="mon-dim">total_tokens=' + t.tokens + "</span>";
        monLog.appendChild(line);
      });
      monTokens.textContent = session.turns[session.turns.length - 1].tokens.toLocaleString();
      var offset = GAUGE_CIRCUMFERENCE * (1 - session.savings / 100);
      monGaugeFill.style.transition = "none";
      monGaugeFill.style.strokeDashoffset = offset;
      monGaugePct.textContent = session.savings.toFixed(1) + "%";
      session.findings.forEach(function (f) {
        var chip = document.createElement("div");
        chip.className = "mon-finding";
        chip.style.opacity = "1";
        chip.style.animation = "none";
        chip.textContent = f;
        monFindings.appendChild(chip);
      });
      return;
    }

    var i = 0;
    function nextTurn() {
      if (i >= session.turns.length) {
        setTimeout(function () {
          var offset = GAUGE_CIRCUMFERENCE * (1 - session.savings / 100);
          monGaugeFill.style.strokeDashoffset = offset;
          var start = performance.now();
          function tick(now) {
            var t = Math.min(1, (now - start) / 900);
            monGaugePct.textContent = (session.savings * t).toFixed(1) + "%";
            if (t < 1) requestAnimationFrame(tick);
          }
          requestAnimationFrame(tick);

          session.findings.forEach(function (f, fi) {
            setTimeout(function () {
              var chip = document.createElement("div");
              chip.className = "mon-finding";
              chip.textContent = f;
              monFindings.appendChild(chip);
            }, fi * 450);
          });
        }, 300);

        setTimeout(function () {
          runMonitor(index + 1);
        }, 300 + session.findings.length * 450 + 4200);
        return;
      }
      var t = session.turns[i];
      var line = document.createElement("div");
      line.className = "mon-log-line";
      line.innerHTML =
        '<span class="mon-turn">turn ' + (i + 1) + "</span> " +
        '<span class="mon-tool">' + t.tool + "</span> " +
        '<span class="mon-dim">total_tokens=' + t.tokens + "</span>";
      monLog.appendChild(line);
      monLog.scrollTop = monLog.scrollHeight;
      monTokens.textContent = t.tokens.toLocaleString();
      i++;
      setTimeout(nextTurn, 650);
    }
    nextTurn();
  }

  if (monLog && monGaugeFill) {
    var auditorMonitorEl = document.getElementById("auditorMonitor");
    var monitorObserver = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            runMonitor(0);
            monitorObserver.disconnect();
          }
        });
      },
      { threshold: 0.3 }
    );
    if (auditorMonitorEl) monitorObserver.observe(auditorMonitorEl);
  }

  /* ---------- frameworks x domains force-directed graph ---------- */
  /* Every edge below is a real measured cell from the live-results matrix
     shown earlier on the page (docs/product/live-results-2026-08-30.md) —
     no synthetic/placeholder connections. A lightweight hand-rolled force
     simulation (no D3/vis dependency) settles the layout, then idles. */

  (function initFwGraph() {
    var canvas = document.getElementById("fwGraph");
    if (!canvas) return;
    var ctx2d = canvas.getContext("2d");
    var tooltip = document.getElementById("fwGraphTooltip");
    var wrap = canvas.closest(".fw-graph-wrap");

    var frameworkNodes = [
      { id: "langgraph", label: "LangGraph", type: "fw" },
      { id: "crewai", label: "CrewAI", type: "fw" },
      { id: "autogen", label: "AutoGen", type: "fw" },
      { id: "openai-agents-sdk", label: "OpenAI Agents SDK", type: "fw" },
    ];
    var domainNodes = [
      { id: "callcenter-ticket", label: "callcenter-ticket", type: "domain" },
      { id: "code-bugfix", label: "code-bugfix", type: "domain" },
      { id: "crm-update", label: "crm-update", type: "domain" },
      { id: "devops-log-triage", label: "devops-log-triage", type: "domain" },
      { id: "ecommerce-order-support", label: "ecommerce-order-support", type: "domain" },
      { id: "it-helpdesk-config", label: "it-helpdesk-config", type: "domain" },
      { id: "legal-clause-lookup", label: "legal-clause-lookup", type: "domain" },
    ];
    // [framework, domain, cost-weighted savings %] — copied verbatim from
    // the matrix table in #results (docs/product/live-results-2026-08-30.md).
    var edgeData = [
      ["langgraph", "callcenter-ticket", 64.4],
      ["langgraph", "code-bugfix", 55.7],
      ["langgraph", "crm-update", 49.9],
      ["langgraph", "devops-log-triage", 19.0],
      ["langgraph", "ecommerce-order-support", 24.4],
      ["langgraph", "it-helpdesk-config", 62.4],
      ["langgraph", "legal-clause-lookup", 69.6],
      ["crewai", "callcenter-ticket", 61.5],
      ["crewai", "code-bugfix", 48.5],
      ["crewai", "crm-update", 36.7],
      ["crewai", "devops-log-triage", 27.8],
      ["crewai", "ecommerce-order-support", 1.7],
      ["crewai", "it-helpdesk-config", 42.1],
      ["crewai", "legal-clause-lookup", 58.7],
      ["openai-agents-sdk", "callcenter-ticket", 66.7],
      ["openai-agents-sdk", "code-bugfix", 53.7],
      ["openai-agents-sdk", "crm-update", 50.3],
      ["openai-agents-sdk", "devops-log-triage", 37.0],
      ["openai-agents-sdk", "ecommerce-order-support", 23.5],
      ["openai-agents-sdk", "it-helpdesk-config", 66.2],
      ["openai-agents-sdk", "legal-clause-lookup", 74.8],
      ["autogen", "callcenter-ticket", 65.8],
      ["autogen", "code-bugfix", 51.0],
      ["autogen", "crm-update", 51.5],
      ["autogen", "devops-log-triage", 22.1],
      ["autogen", "ecommerce-order-support", 28.5],
      ["autogen", "it-helpdesk-config", 64.3],
      ["autogen", "legal-clause-lookup", 70.9],
    ];

    var nodes = frameworkNodes.concat(domainNodes);
    var nodeById = {};
    nodes.forEach(function (n) { nodeById[n.id] = n; });
    var edges = edgeData.map(function (e) {
      return { a: nodeById[e[0]], b: nodeById[e[1]], pct: e[2] };
    });

    var degree = {};
    edges.forEach(function (e) {
      degree[e.a.id] = (degree[e.a.id] || 0) + 1;
      degree[e.b.id] = (degree[e.b.id] || 0) + 1;
    });

    var W = 960, H = 460; // logical layout space, canvas scaled to fit
    nodes.forEach(function (n, i) {
      var angle = (i / nodes.length) * Math.PI * 2;
      var radius = n.type === "fw" ? 130 : 210;
      n.x = W / 2 + Math.cos(angle) * radius + (Math.random() - 0.5) * 20;
      n.y = H / 2 + Math.sin(angle) * radius + (Math.random() - 0.5) * 20;
      n.vx = 0;
      n.vy = 0;
      n.r = n.type === "fw" ? 15 + (degree[n.id] || 0) * 1.2 : 7;
    });

    function resizeCanvas() {
      var rect = wrap.getBoundingClientRect();
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = rect.width * dpr;
      canvas.height = 420 * dpr;
      ctx2d.setTransform(dpr * (rect.width / W), 0, 0, dpr * (420 / H), 0, 0);
    }

    var settleFrames = 0;
    var MAX_SETTLE_FRAMES = reduceMotion ? 1 : 220;
    var dragNode = null;

    function step() {
      var alpha = dragNode ? 1 : Math.max(0.02, 1 - settleFrames / MAX_SETTLE_FRAMES);
      // repulsion
      for (var i = 0; i < nodes.length; i++) {
        for (var j = i + 1; j < nodes.length; j++) {
          var a = nodes[i], b = nodes[j];
          var dx = a.x - b.x, dy = a.y - b.y;
          var dist2 = Math.max(dx * dx + dy * dy, 100);
          var force = (5200 * alpha) / dist2;
          var dist = Math.sqrt(dist2);
          var fx = (dx / dist) * force, fy = (dy / dist) * force;
          a.vx += fx; a.vy += fy;
          b.vx -= fx; b.vy -= fy;
        }
      }
      // springs along edges
      edges.forEach(function (e) {
        var dx = e.b.x - e.a.x, dy = e.b.y - e.a.y;
        var dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
        var rest = e.a.type === "fw" && e.b.type === "fw" ? 260 : 175;
        var k = 0.02 * alpha;
        var f = (dist - rest) * k;
        var fx = (dx / dist) * f, fy = (dy / dist) * f;
        e.a.vx += fx; e.a.vy += fy;
        e.b.vx -= fx; e.b.vy -= fy;
      });
      // centering
      nodes.forEach(function (n) {
        n.vx += (W / 2 - n.x) * 0.004;
        n.vy += (H / 2 - n.y) * 0.004;
        n.vx *= 0.82;
        n.vy *= 0.82;
        n.x += n.vx;
        n.y += n.vy;
        n.x = Math.max(n.r + 8, Math.min(W - n.r - 8, n.x));
        n.y = Math.max(n.r + 8, Math.min(H - n.r - 8, n.y));
      });
      if (dragNode) {
        // pinned node overrides physics — it follows the pointer exactly.
        dragNode.x = dragNode._targetX;
        dragNode.y = dragNode._targetY;
        dragNode.vx = 0;
        dragNode.vy = 0;
      } else {
        settleFrames++;
      }
    }

    var hoverNode = null;

    function draw() {
      ctx2d.clearRect(0, 0, W, H);

      edges.forEach(function (e) {
        var isHoverEdge = hoverNode && (e.a === hoverNode || e.b === hoverNode);
        var t = Math.min(1, e.pct / 75);
        var alpha2 = hoverNode ? (isHoverEdge ? 0.95 : 0.08) : 0.28 + t * 0.5;
        ctx2d.strokeStyle = "rgba(53, 226, 196, " + alpha2 + ")";
        ctx2d.lineWidth = 1 + t * 3.2;
        ctx2d.beginPath();
        ctx2d.moveTo(e.a.x, e.a.y);
        ctx2d.lineTo(e.b.x, e.b.y);
        ctx2d.stroke();
      });

      nodes.forEach(function (n) {
        var dim = hoverNode && hoverNode !== n && !edges.some(function (e) {
          return (e.a === hoverNode && e.b === n) || (e.b === hoverNode && e.a === n);
        });
        ctx2d.globalAlpha = dim ? 0.35 : 1;
        ctx2d.beginPath();
        ctx2d.arc(n.x, n.y, n === hoverNode || n === dragNode ? n.r + 3 : n.r, 0, Math.PI * 2);
        ctx2d.fillStyle = n.type === "fw" ? "#7c5cff" : "#35e2c4";
        ctx2d.fill();
        if (n === dragNode) {
          ctx2d.lineWidth = 2.5;
          ctx2d.strokeStyle = "#fff";
          ctx2d.stroke();
        } else if (n.type === "fw") {
          ctx2d.lineWidth = 2;
          ctx2d.strokeStyle = "rgba(255,255,255,0.25)";
          ctx2d.stroke();
        }
        ctx2d.globalAlpha = dim ? 0.45 : 1;
        ctx2d.fillStyle = n.type === "fw" ? "#f1efff" : "#9fb0c9";
        ctx2d.font = (n.type === "fw" ? "650 12px " : "500 10.5px ") +
          getComputedStyle(document.body).fontFamily;
        ctx2d.textAlign = "center";
        ctx2d.fillText(n.label, n.x, n.y - n.r - 7);
        ctx2d.globalAlpha = 1;
      });
    }

    var running = false;
    function loop() {
      if (dragNode || settleFrames < MAX_SETTLE_FRAMES) {
        step();
        draw();
        requestAnimationFrame(loop);
      } else {
        draw();
        running = false;
      }
    }

    function startIfNeeded() {
      if (running) return;
      running = true;
      settleFrames = Math.min(settleFrames, MAX_SETTLE_FRAMES - 1);
      requestAnimationFrame(loop);
    }

    resizeCanvas();
    draw();

    var graphObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          startIfNeeded();
        }
      });
    }, { threshold: 0.2 });
    graphObserver.observe(wrap);

    window.addEventListener("resize", function () {
      resizeCanvas();
      draw();
    });

    function toLogicalPoint(clientX, clientY) {
      var rect = canvas.getBoundingClientRect();
      return {
        x: ((clientX - rect.left) / rect.width) * W,
        y: ((clientY - rect.top) / rect.height) * H,
      };
    }

    function nodeAt(pt) {
      var found = null;
      nodes.forEach(function (n) {
        var dx = n.x - pt.x, dy = n.y - pt.y;
        if (Math.sqrt(dx * dx + dy * dy) < n.r + 10) found = n;
      });
      return found;
    }

    function showTooltip(node, clientX, clientY) {
      var wrapRect = wrap.getBoundingClientRect();
      var connected = edges
        .filter(function (e) { return e.a === node || e.b === node; })
        .map(function (e) {
          var other = e.a === node ? e.b : e.a;
          return '<div class="fgt-row">' + other.label + " — +" + e.pct.toFixed(1) + "%</div>";
        })
        .join("");
      tooltip.innerHTML = '<div class="fgt-title">' + node.label + "</div>" + connected;
      tooltip.style.left = clientX - wrapRect.left + "px";
      tooltip.style.top = clientY - wrapRect.top + "px";
      tooltip.classList.add("active");
    }

    canvas.style.touchAction = "none";

    canvas.addEventListener("pointerdown", function (e) {
      var pt = toLogicalPoint(e.clientX, e.clientY);
      var found = nodeAt(pt);
      if (!found) return;
      e.preventDefault();
      canvas.setPointerCapture(e.pointerId);
      dragNode = found;
      dragNode._targetX = pt.x;
      dragNode._targetY = pt.y;
      hoverNode = found;
      canvas.classList.add("dragging");
      showTooltip(found, e.clientX, e.clientY);
      startIfNeeded();
    });

    canvas.addEventListener("pointermove", function (e) {
      var pt = toLogicalPoint(e.clientX, e.clientY);
      if (dragNode) {
        dragNode._targetX = Math.max(dragNode.r + 8, Math.min(W - dragNode.r - 8, pt.x));
        dragNode._targetY = Math.max(dragNode.r + 8, Math.min(H - dragNode.r - 8, pt.y));
        showTooltip(dragNode, e.clientX, e.clientY);
        return;
      }
      if (!finePointer) return;
      var found = nodeAt(pt);
      if (found !== hoverNode) {
        hoverNode = found;
        draw();
      }
      canvas.style.cursor = found ? "grab" : "default";
      if (found) {
        showTooltip(found, e.clientX, e.clientY);
      } else {
        tooltip.classList.remove("active");
      }
    });

    function endDrag() {
      if (!dragNode) return;
      dragNode = null;
      settleFrames = 0; // let the network gently re-settle around the new position
      startIfNeeded();
    }

    canvas.addEventListener("pointerup", function () {
      endDrag();
      canvas.classList.remove("dragging");
    });
    canvas.addEventListener("pointercancel", function () {
      endDrag();
      canvas.classList.remove("dragging");
    });

    if (finePointer) {
      canvas.addEventListener("mouseleave", function () {
        if (dragNode) return;
        hoverNode = null;
        tooltip.classList.remove("active");
        draw();
      });
    }
  })();
})();

/* ---------- quickstart: framework tabs + copy-to-clipboard ---------- */
(function () {
  const snippets = {
    crewai: {
      install: 'pip install "contextos-auditor[crewai]"',
      code: 'from contextos_auditor.crewai import attach\naudit = attach(task="fix the bug")\ncrew.kickoff()\naudit.detach()',
    },
    langgraph: {
      install: 'pip install "contextos-auditor[langgraph]"',
      code: 'from contextos_auditor.langgraph import AuditorCallback\nhandler = AuditorCallback(task="fix the bug")\ngraph.invoke({"messages": [...]}, config={"callbacks": [handler]})\nhandler.finish()',
    },
    openai: {
      install: 'pip install "contextos-auditor[openai-agents]"',
      code: 'from contextos_auditor.openai_agents import attach\naudit = attach(task="fix the bug")\nresult = await Runner.run(agent, "do the thing")\naudit.detach()',
    },
    autogen: {
      install: 'pip install "contextos-auditor[autogen]"',
      code: 'from contextos_auditor.autogen import new_session, wrap_client, audit_tool\nsession = new_session(task="fix the bug")\nclient = wrap_client(real_client, session)\nagent = AssistantAgent("coder", model_client=client, tools=[audit_tool(write_file, session)])\n...\nsession.finish()',
    },
  };

  const tabs = document.querySelectorAll(".qs-tab");
  const installEl = document.getElementById("qsInstall");
  const snippetEl = document.getElementById("qsSnippet");
  if (!tabs.length || !installEl || !snippetEl) return;

  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      tabs.forEach(function (t) {
        t.classList.remove("active");
        t.setAttribute("aria-selected", "false");
      });
      tab.classList.add("active");
      tab.setAttribute("aria-selected", "true");
      const fw = tab.getAttribute("data-fw");
      const s = snippets[fw];
      if (!s) return;
      installEl.textContent = s.install;
      snippetEl.textContent = s.code;
    });
  });

  document.querySelectorAll(".copy-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const block = btn.closest(".code-block");
      const code = block && block.querySelector("code");
      if (!code) return;
      const text = code.textContent;
      const done = function () {
        const original = btn.textContent;
        btn.textContent = "Copied";
        btn.classList.add("copied");
        setTimeout(function () {
          btn.textContent = original;
          btn.classList.remove("copied");
        }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(done);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); } catch (e) {}
        document.body.removeChild(ta);
        done();
      }
    });
  });
})();
