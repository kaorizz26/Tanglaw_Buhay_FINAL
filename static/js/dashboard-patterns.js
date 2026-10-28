"use strict";

const patterns = document.querySelector("[data-patterns]");

function showArea(key) {
  patterns.querySelectorAll(".pattern-area").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.area === key));
  });
  patterns.querySelectorAll(".pattern-panel").forEach((panel) => {
    panel.hidden = panel.id !== `pattern-panel-${key}`;
  });
  keepAreaInFilter(key);
}

// The barangay form and its reset link carry the chosen area, so it survives a new filter.
function keepAreaInFilter(key) {
  const field = document.querySelector("#dashboard-area");
  if (field) {
    field.value = key;
  }
  const reset = document.querySelector("[data-keep-area]");
  if (reset) {
    const link = new URL(reset.href);
    link.searchParams.set("area", key);
    reset.href = link.pathname + link.search;
  }
}

if (patterns) {
  patterns.querySelectorAll(".pattern-area").forEach((button) => {
    button.addEventListener("click", () => showArea(button.dataset.area));
  });
}
