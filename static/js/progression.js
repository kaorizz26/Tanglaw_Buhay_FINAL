"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const filter = document.getElementById("indicator-filter");
  if (!filter) return;
  const rows = [...document.querySelectorAll("[data-change]")];
  const status = document.getElementById("indicator-filter-status");
  const update = () => {
    const selected = filter.value;
    let visible = 0;
    for (const row of rows) {
      row.hidden = selected !== "all" && row.dataset.change !== selected;
      if (!row.hidden) visible += 1;
    }
    for (const section of document.querySelectorAll(".progression-dimension")) {
      section.hidden = !section.querySelector("tr[data-change]:not([hidden])");
    }
    status.textContent = visible
      ? `${visible} indicator${visible === 1 ? "" : "s"} shown.`
      : "No indicators have that change status.";
  };
  filter.addEventListener("change", update);
  update();
});
