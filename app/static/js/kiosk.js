// Kiosk client: local elapsed-timer rendering + wake lock (technical plan §3.1, spec §8.3).
// Phase 1 shows a plain elapsed timer (run-1 behaviour); countdown rings arrive in Phase 2.

(function () {
  let wakeLock = null;

  async function acquireWakeLock() {
    try {
      if ("wakeLock" in navigator) {
        wakeLock = await navigator.wakeLock.request("screen");
      }
    } catch (_) { /* best effort */ }
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") acquireWakeLock();
  });
  acquireWakeLock();

  function fmt(totalSeconds) {
    const m = Math.floor(totalSeconds / 60);
    const s = totalSeconds % 60;
    return m + ":" + String(s).padStart(2, "0");
  }

  // Render elapsed time from server-stamped first_started_at (epoch ms).
  function tick() {
    const now = Date.now();
    document.querySelectorAll(".timer[data-started]").forEach((el) => {
      const started = parseInt(el.dataset.started, 10);
      if (!started) return;
      const elapsed = Math.max(0, Math.floor((now - started) / 1000));
      el.textContent = fmt(elapsed);
    });
  }
  setInterval(tick, 250);
  tick();
})();
