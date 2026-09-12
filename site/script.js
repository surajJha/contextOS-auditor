/* Native DOM, CSS 3D, and Canvas. No libraries or network requests. */
(function () {
  "use strict";

  const root = document.documentElement;
  const motionPreference = matchMedia("(prefers-reduced-motion: reduce)");
  const observatory = document.getElementById("observatory");
  const motionButton = document.getElementById("motionToggle");
  let paused = motionPreference.matches;
  let sceneVisible = false;
  let frame = 0;
  let phase = 0;
  let lastFrame = 0;
  let stage = "audit";
  const stageCopy = {
    baseline: {
      title: "A useful agent. An expanding context.",
      body: "Instructions, tool menus, history, and results travel through the loop. Large payloads and tool overhead can cost tokens even when no call is repeated.",
      finding: "Every layer comes along.",
    },
    audit: {
      title: "Find opportunities across the run.",
      body: "The Auditor shows where tokens go and estimates opportunities in tool payloads, menus, and calls. It observes your agent; it does not change it.",
      finding: "Avoidable work, revealed.",
    },
    optimise: {
      title: "Change the work, not the goal.",
      body: "Smaller edit payloads, selective reads, bounded searches, scoped tools, and fewer unnecessary calls. Instructions and history are not rewritten by this tool layer.",
      finding: "Focused tools. Leaner payloads.",
    },
  };

  document.querySelectorAll(".stage-controls button").forEach((button) => {
    button.addEventListener("click", () => {
      stage = button.dataset.stage;
      observatory.dataset.stage = stage;
      document.querySelectorAll(".stage-controls button").forEach((other) => {
        other.setAttribute("aria-pressed", String(other === button));
      });
      document.getElementById("sceneTitle").textContent = stageCopy[stage].title;
      document.getElementById("sceneCopy").textContent = stageCopy[stage].body;
      document.getElementById("sceneFinding").textContent = stageCopy[stage].finding;
      drawField();
    });
  });

  const canvas = document.getElementById("contextField");
  const context = canvas.getContext("2d");
  let width = 0;
  let height = 0;
  const particles = Array.from({ length: 58 }, (_, i) => ({
    angle: i * 2.39996,
    radius: 0.63 + (i % 7) * 0.048,
    offset: Math.sin(i * 1.71) * 95,
    size: i % 5 === 0 ? 2 : 1,
  }));

  function drawField() {
    if (!context || !width || !height) return;
    context.clearRect(0, 0, width, height);
    context.strokeStyle = "rgba(130,145,190,.06)";
    context.lineWidth = 1;
    for (let x = 0; x < width; x += 32) {
      context.beginPath(); context.moveTo(x, 0); context.lineTo(x, height); context.stroke();
    }
    for (let y = 0; y < height; y += 32) {
      context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke();
    }
    const points = particles.slice(0, stage === "optimise" ? 32 : 58).map((particle) => {
      const angle = particle.angle + phase;
      const z = Math.sin(angle) * 110;
      const perspective = 500 / (500 + z);
      return {
        x: width / 2 + Math.cos(angle) * width * .47 * particle.radius * perspective,
        y: height / 2 + (Math.sin(angle) * 72 + particle.offset) * perspective,
        alpha: .2 + .55 * (1 - (z + 110) / 220),
        size: particle.size * perspective,
      };
    });
    points.forEach((point, i) => {
      const amber = stage === "audit" && i % 4 === 0;
      const color = stage === "optimise" ? "53,226,196" : amber ? "255,180,110" : "159,134,249";
      const next = points[(i + 7) % points.length];
      if (Math.hypot(next.x - point.x, next.y - point.y) < 100) {
        context.strokeStyle = "rgba(" + color + ",.1)";
        context.beginPath(); context.moveTo(point.x, point.y); context.lineTo(next.x, next.y); context.stroke();
      }
      context.fillStyle = "rgba(" + color + "," + point.alpha + ")";
      context.beginPath(); context.arc(point.x, point.y, point.size, 0, Math.PI * 2); context.fill();
    });
  }

  function animate(now) {
    frame = 0;
    if (paused || !sceneVisible || document.hidden || !context) return;
    if (now - lastFrame > 33) {
      phase += Math.min(now - lastFrame, 80) * .00009;
      lastFrame = now;
      drawField();
    }
    frame = requestAnimationFrame(animate);
  }

  function syncMotion() {
    cancelAnimationFrame(frame);
    frame = 0;
    root.dataset.motion = paused ? "paused" : "running";
    motionButton.textContent = paused ? "Play motion" : "Pause motion";
    motionButton.setAttribute("aria-pressed", String(paused));
    if (!paused && sceneVisible && !document.hidden && context) {
      lastFrame = performance.now();
      frame = requestAnimationFrame(animate);
    }
  }

  function resizeField() {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(devicePixelRatio || 1, 2);
    width = rect.width;
    height = rect.height;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    if (context) context.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawField();
  }
  motionButton.addEventListener("click", () => { paused = !paused; syncMotion(); });
  motionPreference.addEventListener("change", () => { paused = motionPreference.matches; syncMotion(); });
  document.addEventListener("visibilitychange", syncMotion);
  new IntersectionObserver((entries) => {
    sceneVisible = entries[0].isIntersecting;
    observatory.classList.toggle("offscreen", !sceneVisible);
    syncMotion();
  }).observe(observatory);
  new ResizeObserver(resizeField).observe(canvas);
  resizeField();
  syncMotion();

  const levers = {
    edits: {
      before: "Rewrite the entire file", after: "Send the relevant change",
      title: "A small change should not need a full rewrite.",
      copy: "Anchored edits can replace a small region instead of asking the model to emit the entire updated file.",
      boundary: "The edit must match safely. Ambiguous changes need more context, not a blind replacement.",
      inputTiles: 24, outputTiles: 4,
    },
    reads: {
      before: "Return every line", after: "Start with useful structure",
      title: "Read what helps the next decision.",
      copy: "For supported file shapes, a structural view can orient the agent without returning every implementation detail upfront.",
      boundary: "Full content remains available when needed. Not every file can or should be reduced to a structural view.",
      inputTiles: 24, outputTiles: 8,
    },
    search: {
      before: "Flood the context with matches", after: "Group and bound the results",
      title: "Find the answer, not a wall of text.",
      copy: "Grouped matches and output budgets can keep a search response focused instead of filling the next model request with sprawling results.",
      boundary: "A bounded result can require a narrower follow-up query. Useful evidence must not be mistaken for complete coverage.",
      inputTiles: 24, outputTiles: 6,
    },
    schemas: {
      before: "Offer the whole toolbox", after: "Scope tools to the task",
      title: "Your agent does not need every tool, every time.",
      copy: "Task-specific tool profiles and concise descriptions reduce the schema text sent with model requests.",
      boundary: "The profile must still include the tools the task needs. A smaller menu that prevents completion is not a saving.",
      inputTiles: 12, outputTiles: 4,
    },
    calls: {
      before: "Spend a call on known work", after: "Skip eligible redundant calls",
      title: "Avoid a call when it adds no useful information.",
      copy: "Duplicate-call guards and post-write confirmations can avoid eligible repeated work and unnecessary verification reads.",
      boundary: "Changed state and calls outside the guard window may need fresh execution. There is no fixed number of calls or tokens saved.",
      inputTiles: 6, outputTiles: 4,
    },
  };

  function showLever(button) {
    const name = button.dataset.lever;
    const lever = levers[name];
    document.getElementById("leverDiagram").dataset.lever = name;
    [
      ["payloadBeforeLabel", lever.before], ["payloadAfterLabel", lever.after],
      ["leverTitle", lever.title], ["leverCopy", lever.copy], ["leverBoundary", lever.boundary],
    ].forEach(([id, text]) => { document.getElementById(id).textContent = text; });
    [["payloadBefore", lever.inputTiles], ["payloadAfter", lever.outputTiles]].forEach(([id, count]) => {
      const payload = document.getElementById(id);
      payload.replaceChildren();
      for (let i = 0; i < count; i++) {
        const tile = document.createElement("i");
        tile.className = "payload-tile";
        tile.style.setProperty("--tile-index", i);
        payload.appendChild(tile);
      }
    });
    document.querySelectorAll("[data-lever][aria-pressed]").forEach((other) => {
      other.setAttribute("aria-pressed", String(other === button));
    });
  }
  const leverButtons = [...document.querySelectorAll("[data-lever][aria-pressed]")];
  leverButtons.forEach((button) => button.addEventListener("click", () => showLever(button)));
  showLever(leverButtons[0]);

  const slider = document.getElementById("turnSlider");
  function updateReplay() {
    const turns = Number(slider.value);
    const total = turns * (turns + 1) / 2;
    const chart = document.getElementById("replayChart");
    chart.replaceChildren();
    for (let i = 1; i <= turns; i++) {
      const column = document.createElement("div");
      column.className = "replay-column";
      for (let unit = 0; unit < i; unit++) {
        const block = document.createElement("i");
        block.className = "replay-block" + (unit === i - 1 ? " new" : "");
        column.appendChild(block);
      }
      const label = document.createElement("span");
      label.textContent = "T" + i;
      column.appendChild(label);
      chart.appendChild(column);
    }
    document.getElementById("turnCount").textContent = turns;
    document.getElementById("contextUnits").textContent = total;
    document.getElementById("replayUnits").textContent = (total - turns) + " replayed + " + turns + " new";
    slider.setAttribute("aria-valuetext", turns + " turns, " + total + " context units processed");
  }
  slider.addEventListener("input", updateReplay);
  updateReplay();

  // The visible matrix is the source for every chart value, including caveats.
  const table = document.getElementById("benchmarkMatrix");
  const benchmarkButtons = [...document.querySelectorAll("[data-framework][data-total]")];
  function showBenchmark(button) {
    const column = [...table.tHead.rows[0].cells].findIndex((cell) => cell.dataset.framework === button.dataset.framework);
    document.getElementById("benchmarkName").textContent = button.textContent.toUpperCase();
    const total = document.getElementById("benchmarkTotal");
    total.replaceChildren(document.createTextNode(button.dataset.total));
    const percent = document.createElement("span");
    percent.textContent = "%";
    total.appendChild(percent);
    const chart = document.getElementById("benchmarkChart");
    [...table.tBodies[0].rows].forEach((row, index) => {
      const valueText = row.cells[column].textContent;
      const existing = chart.children[index];
      if (existing) {
        existing.querySelector(".benchmark-fill").style.width = parseFloat(valueText) + "%";
        existing.querySelector("b").textContent = valueText;
        return;
      }
      const entry = document.createElement("div");
      entry.className = "benchmark-row";
      const label = document.createElement("span");
      label.textContent = row.cells[0].textContent;
      const track = document.createElement("div");
      track.className = "benchmark-track";
      track.setAttribute("aria-hidden", "true");
      const fill = document.createElement("div");
      fill.className = "benchmark-fill";
      fill.style.width = parseFloat(valueText) + "%";
      track.appendChild(fill);
      const value = document.createElement("b");
      value.textContent = valueText;
      entry.append(label, track, value);
      chart.appendChild(entry);
    });
    benchmarkButtons.forEach((other) => other.setAttribute("aria-pressed", String(other === button)));
    chart.setAttribute("aria-label", button.textContent + " historical cost-weighted savings by domain. Dagger marks raw-token uncertainty.");
  }
  benchmarkButtons.forEach((button) => button.addEventListener("click", () => showBenchmark(button)));
  showBenchmark(benchmarkButtons[0]);

  const snippets = {
    crewai: 'from contextos_auditor.crewai import attach\n\naudit = attach(task="my agent run")\ntry:\n    crew.kickoff()\nfinally:\n    audit.detach()',
    langgraph: 'from contextos_auditor.langgraph import AuditorCallback\n\nhandler = AuditorCallback(task="my agent run")\ntry:\n    graph.invoke(inputs, config={"callbacks": [handler]})\nfinally:\n    handler.finish()',
    openai: 'from contextos_auditor.openai_agents import attach\n\naudit = attach(task="my agent run")\ntry:\n    result = await Runner.run(agent, "my task")\nfinally:\n    audit.detach()',
    autogen: 'from contextos_auditor.autogen import new_session, wrap_client, audit_tool\n\nsession = new_session(task="my agent run")\nclient = wrap_client(real_client, session)\nagent = AssistantAgent("assistant", model_client=client,\n    tools=[audit_tool(tool_fn, session)])\ntry:\n    await agent.run(task="my task")\nfinally:\n    session.finish()',
  };
  const tabs = [...document.querySelectorAll(".qs-tab")];
  function selectTab(tab, focus) {
    tabs.forEach((other) => {
      other.setAttribute("aria-selected", String(other === tab));
      other.tabIndex = other === tab ? 0 : -1;
    });
    document.getElementById("quickstartCode").setAttribute("aria-labelledby", tab.id);
    document.getElementById("qsSnippet").textContent = snippets[tab.dataset.fw];
    if (focus) tab.focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectTab(tab, false));
    tab.addEventListener("keydown", (event) => {
      let next;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (next === undefined) return;
      event.preventDefault();
      selectTab(tabs[next], true);
    });
  });
  document.querySelectorAll(".copy-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      const code = button.closest(".code-block").querySelector("code");
      const status = document.getElementById("copyStatus");
      try {
        if (!navigator.clipboard) throw new Error("Clipboard is unavailable");
        await navigator.clipboard.writeText(code.textContent);
        status.textContent = "Copied to clipboard.";
      } catch {
        const range = document.createRange();
        range.selectNodeContents(code);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        status.textContent = "Clipboard unavailable. Code selected; press Ctrl+C or Command+C to copy.";
      }
    });
  });
})();
