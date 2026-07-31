// Kiosk client: local countdown-ring rendering + colour states + wake lock.
// The server is authoritative (spec §5.7); the kiosk renders elapsed vs par
// locally from server-stamped first_started_at and the snapshotted par.

(function () {
  let wakeLock = null;
  async function acquireWakeLock() {
    try {
      if ("wakeLock" in navigator) wakeLock = await navigator.wakeLock.request("screen");
    } catch (_) { /* best effort */ }
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") acquireWakeLock();
  });
  acquireWakeLock();

  const R = 52;
  const CIRC = 2 * Math.PI * R;

  // Server debounces completions under MIN_TASK_SECONDS; this only disables the
  // button so a child gets visual feedback instead of a silently-ignored tap.
  const MIN_TASK_SECONDS = 30;

  function tickDoneButtons() {
    const now = Date.now();
    document.querySelectorAll(".done-btn[data-debounce-until]").forEach((btn) => {
      const started = parseInt(btn.dataset.debounceUntil, 10);
      if (!started) return;
      const ready = (now - started) / 1000 >= MIN_TASK_SECONDS;
      btn.disabled = !ready;
      btn.classList.toggle("done-btn-waiting", !ready);
    });
  }

  function fmt(sec) {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return m + ":" + String(s).padStart(2, "0");
  }

  // Colour state thresholds (spec §5.5).
  function stateFor(ratio) {
    if (ratio < 0.75) return "on_pace";
    if (ratio <= 1.0) return "closing";
    if (ratio <= 1.5) return "over";
    return "stalled";
  }

  function tick() {
    const now = Date.now();
    document.querySelectorAll(".ring[data-started]").forEach((svg) => {
      const started = parseInt(svg.dataset.started, 10);
      const par = parseInt(svg.dataset.par, 10) || 0;
      const numeric = svg.dataset.mode === "ring_numeric";
      if (!started) return;
      const elapsed = Math.max(0, Math.floor((now - started) / 1000));

      const arc = svg.querySelector(".ring-arc");
      const text = svg.querySelector(".ring-text");
      arc.style.strokeDasharray = CIRC;

      if (par > 0) {
        const ratio = elapsed / par;
        const remaining = Math.max(0, par - elapsed);
        // draining arc: full at start, empty at par; then it counts up over-par
        const frac = Math.max(0, Math.min(1, remaining / par));
        arc.style.strokeDashoffset = CIRC * (1 - frac);
        const st = stateFor(ratio);
        svg.setAttribute("data-state", st);
        // Age 8 sees numbers (remaining, then +over); age 5 sees the arc only.
        if (numeric) {
          text.textContent = ratio <= 1.0 ? fmt(remaining) : "+" + fmt(elapsed - par);
          text.style.display = "";
        } else {
          text.style.display = "none";
        }
      } else {
        // run 1: no par — plain elapsed timer, neutral (spec §5.2)
        arc.style.strokeDashoffset = 0;
        svg.setAttribute("data-state", "none");
        text.textContent = numeric ? fmt(elapsed) : "";
        text.style.display = numeric ? "" : "none";
      }
    });
  }
  function tickAll() {
    tick();
    tickDoneButtons();
  }
  setInterval(tickAll, 250);
  tickAll();

  // Recompute immediately after any HTMX swap (e.g. a column refreshing
  // itself) so a freshly-rendered ring/button never flashes stale before the
  // next interval — each child's timer stays visually stable and independent.
  document.body.addEventListener("htmx:afterSettle", tickAll);
  document.body.addEventListener("htmx:load", tickAll);
})();
