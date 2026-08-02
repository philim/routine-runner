// Drag-to-reorder for the step-edit table (spec §12).
//
// Pointer-events based rather than the native HTML5 drag API so it works with
// a thumb on a phone — the parent config surface is explicitly one-handed at
// 22:40 — as well as with a mouse. Rows reorder live as the pointer crosses
// each neighbour's midpoint; on release the new order POSTs to /steps/reorder
// and htmx swaps the re-rendered (and server-validated) table back in.
//
// Listeners are delegated on the document, so nothing needs re-wiring when
// htmx replaces #steps-table after an edit.
(function () {
  "use strict";

  let state = null;

  function rowAfter(container, y, dragged) {
    const rows = Array.prototype.slice
      .call(container.querySelectorAll("tr[data-step-id]"))
      .filter(function (r) { return r !== dragged; });
    for (let i = 0; i < rows.length; i++) {
      const box = rows[i].getBoundingClientRect();
      if (y < box.top + box.height / 2) return rows[i];
    }
    return null; // past the last row → append to the end
  }

  function persist(container) {
    const ids = Array.prototype.slice
      .call(container.querySelectorAll("tr[data-step-id]"))
      .map(function (r) { return r.dataset.stepId; });
    const routineId = container.dataset.routineId;
    const url = container.dataset.reorderUrl;
    if (!routineId || !url || !window.htmx) return;
    window.htmx.ajax("POST", url, {
      target: "#steps-table",
      swap: "outerHTML",
      values: { routine_id: routineId, ordered_ids: ids.join(",") },
    });
  }

  function onMove(e) {
    if (!state) return;
    e.preventDefault();
    state.moved = true;
    const container = state.container;
    const row = state.row;
    const after = rowAfter(container, e.clientY, row);
    if (after == null) {
      if (container.lastElementChild !== row) container.appendChild(row);
    } else if (after !== row && after !== row.nextElementSibling) {
      container.insertBefore(row, after);
    }
  }

  function onUp() {
    if (!state) return;
    const s = state;
    state = null;
    s.row.classList.remove("ring", "ring-primary", "bg-base-200");
    try { s.handle.releasePointerCapture(s.pointerId); } catch (_) {}
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    window.removeEventListener("pointercancel", onUp);
    if (s.moved) persist(s.container);
  }

  function onDown(e) {
    if (e.button != null && e.button !== 0) return; // left / primary only
    const handle = e.target.closest && e.target.closest("[data-drag-handle]");
    if (!handle) return;
    const row = handle.closest("tr[data-step-id]");
    const container = row && row.closest("[data-sortable]");
    if (!row || !container) return;
    e.preventDefault();
    state = { row: row, container: container, handle: handle, pointerId: e.pointerId, moved: false };
    try { handle.setPointerCapture(e.pointerId); } catch (_) {}
    row.classList.add("ring", "ring-primary", "bg-base-200");
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  }

  document.addEventListener("pointerdown", onDown);
})();
