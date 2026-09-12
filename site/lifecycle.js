/* A deterministic guided illustration. All narration lives in the HTML transcript. */
(function () {
  "use strict";

  const section = document.getElementById("lifecycle");
  const player = document.getElementById("lifecyclePlayer");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const chapters = [...section.querySelectorAll("[data-life-chapter]")].map((element) => ({
    title: element.querySelector("h4").textContent,
    agent: element.querySelector("[data-life-agent]").textContent,
    optimiser: element.querySelector("[data-life-optimiser]").textContent,
    baseline: element.querySelector("[data-life-baseline]").textContent,
    auditor: element.querySelector("[data-life-auditor]").textContent,
    boundary: element.querySelector("[data-life-boundary]").textContent,
    beats: [...element.querySelectorAll("[data-life-beats] li")].map((beat) => beat.textContent),
    layers: element.dataset.layers.split(" "),
    optLayers: element.dataset.optLayers.split(" "),
    route: element.dataset.route.split(",").map(Number),
    observer: Number(element.dataset.observer),
    tap: Number(element.dataset.tap),
  }));
  const secondsPerChapter = Number(section.dataset.chapterSeconds);
  const duration = chapters.length * secondsPerChapter;
  const layers = [...section.querySelectorAll("[data-life-layer]")];
  const observers = [...section.querySelectorAll("[data-life-observer]")];
  const jumps = [...section.querySelectorAll("[data-life-jump]")];
  const modes = [...section.querySelectorAll("[data-life-mode]")];
  const rotors = [...section.querySelectorAll("[data-life-rotor]")];
  const packets = [...section.querySelectorAll(".life-packet")];
  const flow = document.getElementById("lifecycleFlow");
  const tap = document.getElementById("lifecycleTap");
  const auditPacket = document.getElementById("lifecycleAuditPacket");
  const seek = document.getElementById("lifecycleSeek");
  const play = document.getElementById("lifecyclePlay");
  const explode = document.getElementById("lifecycleExplode");
  const clock = document.getElementById("lifecycleTime");
  const announcement = document.getElementById("lifecycleAnnouncement");
  const ports = layers.map(() => {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    document.getElementById("lifecyclePorts").appendChild(path);
    return path;
  });
  let time = 0;
  let speed = 1;
  let requested = !reducedMotion.matches;
  let visible = false;
  let frame = 0;
  let lastFrame = 0;
  let currentChapter = -1;
  let currentBeat = -1;
  let mode = "optimised";
  let spread = reducedMotion.matches ? 1 : 0;
  let targetSpread = 1;
  let flowLength = 0;
  let tapLength = 0;

  function chapterIndex() {
    return Math.min(chapters.length - 1, Math.floor(time / secondsPerChapter));
  }

  function formatTime(value) {
    const seconds = Math.floor(value);
    return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
  }

  function announce(message) {
    announcement.textContent = message + " Chapter " + (chapterIndex() + 1) + ": " + chapters[chapterIndex()].title;
  }

  function renderLayout() {
    // One geometry calculation keeps exploded plates, ports, and both paths aligned.
    const positions = layers.map((_, index) => 245 * (1 - spread) + 90 * spread + index * (22 + 53 * spread));
    layers.forEach((layer, index) => {
      layer.style.transform = "translate(135px," + positions[index] + "px)";
      ports[index].setAttribute("d", "M112 " + (positions[index] + 22) + "H135");
    });
    document.getElementById("lifecycleSpine").setAttribute("d", "M112 " + (positions[0] + 22) + "V" + (positions[6] + 22));
    const chapter = chapters[chapterIndex()];
    let path = "M135 " + (positions[chapter.route[0]] + 22);
    chapter.route.slice(1).forEach((index) => { path += "H87V" + (positions[index] + 22) + "H135"; });
    flow.setAttribute("d", path);
    tap.setAttribute("d", "M553 " + (positions[chapter.tap] + 22) + "H601V" + (136 + chapter.observer * 73) + "H635");
    flowLength = flow.getTotalLength();
    tapLength = tap.getTotalLength();
  }

  function renderChapter(force = false) {
    const index = chapterIndex();
    const chapter = chapters[index];
    if (index !== currentChapter || force) {
      currentChapter = index;
      [
        ["lifecycleTitle", chapter.title], ["lifecycleAgent", chapter.agent],
        ["lifecycleOptimiser", mode === "optimised" ? chapter.optimiser : chapter.baseline],
        ["lifecycleAuditor", chapter.auditor], ["lifecycleBoundary", chapter.boundary],
        ["lifecycleOptimiserLabel", mode === "optimised" ? "Optimiser / changes execution" : "Baseline / original tools"],
        ["lifecycleChapterCount", "CHAPTER " + String(index + 1).padStart(2, "0") + " / " + String(chapters.length).padStart(2, "0")],
        ["lifecycleSceneLabel", String(index + 1).padStart(2, "0") + " / " + jumps[index].lastChild.textContent.trim().toUpperCase()],
      ].forEach(([id, text]) => { document.getElementById(id).textContent = text; });
      jumps.forEach((button, i) => button.setAttribute("aria-pressed", String(i === index)));
      layers.forEach((layer) => {
        layer.classList.toggle("active", chapter.layers.includes(layer.dataset.lifeLayer));
        layer.classList.toggle("intervening", mode === "optimised" && chapter.optLayers.includes(layer.dataset.lifeLayer));
      });
      observers.forEach((node, i) => node.classList.toggle("active", i === chapter.observer));
      renderLayout();
    }
    const beat = Math.min(chapter.beats.length - 1, Math.floor((time - index * secondsPerChapter) / (secondsPerChapter / chapter.beats.length)));
    const beatKey = index * 10 + beat;
    if (beatKey !== currentBeat || force) {
      currentBeat = beatKey;
      document.getElementById("lifecycleBeat").textContent = mode === "baseline" && index > 0 && index < 7
        ? chapter.baseline : chapter.beats[beat];
    }
  }

  function renderPosition() {
    seek.value = time;
    seek.setAttribute("aria-valuetext", formatTime(time) + " of " + formatTime(duration) + ", chapter " + (chapterIndex() + 1) + ": " + chapters[chapterIndex()].title);
    clock.textContent = formatTime(time) + " / " + formatTime(duration);
    const phase = reducedMotion.matches ? .45 : (time % secondsPerChapter) / secondsPerChapter;
    packets.forEach((packet, i) => {
      const point = flow.getPointAtLength(((phase * 3 + i * .45) % 1) * flowLength);
      packet.setAttribute("cx", point.x);
      packet.setAttribute("cy", point.y);
    });
    const point = tap.getPointAtLength(((phase * 3 + .2) % 1) * tapLength);
    auditPacket.setAttribute("cx", point.x);
    auditPacket.setAttribute("cy", point.y);
    rotors.forEach((rotor, i) => {
      const angle = reducedMotion.matches ? 0 : time * (i % 2 ? -28 : 28);
      rotor.setAttribute("transform", "rotate(" + angle + ")");
    });
  }

  function render(force = false) {
    renderChapter(force);
    renderPosition();
  }

  function animate(now) {
    frame = 0;
    if (!visible || document.hidden) return;
    const elapsed = now - lastFrame;
    if (elapsed >= 1000 / 30) {
      lastFrame = now;
      // Do not fast-forward a tour after a busy frame or a backgrounded tab.
      const delta = Math.min(elapsed, 100) / 1000;
      if (requested) time = (time + delta * speed) % duration;
      if (spread !== targetSpread) {
        const distance = delta / .7;
        spread = targetSpread > spread ? Math.min(targetSpread, spread + distance) : Math.max(targetSpread, spread - distance);
        renderLayout();
      }
      render();
    }
    if (requested || spread !== targetSpread) frame = requestAnimationFrame(animate);
  }

  function syncPlayback() {
    cancelAnimationFrame(frame);
    frame = 0;
    play.textContent = requested ? "Pause tour" : "Play tour";
    player.dataset.playing = String(requested && visible && !document.hidden);
    if (visible && !document.hidden && (requested || spread !== targetSpread)) {
      lastFrame = performance.now();
      frame = requestAnimationFrame(animate);
    }
  }

  function pause() {
    requested = false;
    syncPlayback();
  }

  play.addEventListener("click", () => {
    requested = !requested;
    if (requested && time === duration) time = 0;
    render();
    syncPlayback();
    announce(requested ? "Playing." : "Paused.");
  });
  seek.max = duration;
  seek.addEventListener("input", () => {
    pause();
    time = Number(seek.value);
    render();
  });
  seek.addEventListener("change", () => announce("Paused at selected time."));
  jumps.forEach((button, index) => button.addEventListener("click", () => {
    pause();
    time = index * secondsPerChapter;
    render();
    announce("Paused at selected chapter.");
  }));
  modes.forEach((button) => button.addEventListener("click", () => {
    pause();
    mode = button.dataset.lifeMode;
    player.dataset.mode = mode;
    modes.forEach((other) => other.setAttribute("aria-pressed", String(other === button)));
    render(true);
    announce(mode === "optimised" ? "With Optimiser." : "Baseline tools. Auditor still attached.");
  }));
  explode.addEventListener("click", () => {
    targetSpread = targetSpread ? 0 : 1;
    explode.setAttribute("aria-pressed", String(Boolean(targetSpread)));
    explode.textContent = targetSpread ? "Exploded view" : "Assembled view";
    if (reducedMotion.matches || !visible || document.hidden) {
      spread = targetSpread;
      renderLayout();
      renderPosition();
    }
    syncPlayback();
    announce(targetSpread ? "Layers separated." : "Layers assembled.");
  });
  document.getElementById("lifecycleSpeed").addEventListener("change", (event) => {
    speed = Number(event.target.value);
    announce("Playback speed " + speed + " times.");
  });
  reducedMotion.addEventListener("change", () => {
    if (reducedMotion.matches) {
      requested = false;
      spread = targetSpread;
      renderLayout();
      renderPosition();
    }
    syncPlayback();
  });
  document.addEventListener("visibilitychange", syncPlayback);
  new IntersectionObserver((entries) => {
    visible = entries[0].isIntersecting;
    syncPlayback();
  }).observe(player);
  section.querySelectorAll("[hidden]").forEach((element) => { element.hidden = false; });
  render();
  syncPlayback();
}());
