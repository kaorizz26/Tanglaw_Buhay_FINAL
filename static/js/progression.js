"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const filter = document.getElementById("indicator-filter");
  if (filter) {
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
  }

  const indicatorTable = document.querySelector("[data-indicator-sort-table]");
  if (indicatorTable) {
    const body = indicatorTable.querySelector("[data-indicator-sort-body]");
    const rows = [...indicatorTable.querySelectorAll("[data-indicator-row]")];
    const controls = [...indicatorTable.querySelectorAll("[data-indicator-sort]")];
    let currentKey = "";
    let currentDirection = "";

    controls.forEach((control) => {
      control.addEventListener("click", () => {
        const key = control.dataset.indicatorSort;
        const numeric = control.dataset.sortType === "number";
        const direction = currentKey === key && currentDirection === "desc"
          ? "asc"
          : currentKey === key && currentDirection === "asc"
            ? "desc"
            : numeric ? "desc" : "asc";
        rows.sort((first, second) => {
          const firstValue = numeric ? Number(first.dataset[key]) : first.dataset[key];
          const secondValue = numeric ? Number(second.dataset[key]) : second.dataset[key];
          const comparison = numeric
            ? firstValue - secondValue
            : firstValue.localeCompare(secondValue, undefined, { sensitivity: "base" });
          return direction === "asc" ? comparison : -comparison;
        });
        rows.forEach((row) => body.append(row));
        controls.forEach((candidate) => {
          const active = candidate === control;
          candidate.classList.toggle("is-active", active);
          candidate.closest("th").setAttribute(
            "aria-sort",
            active ? (direction === "asc" ? "ascending" : "descending") : "none",
          );
          candidate.querySelector("span").textContent = active
            ? (direction === "asc" ? "▲" : "▼")
            : "↕";
        });
        currentKey = key;
        currentDirection = direction;
      });
    });
  }

  const forms = [
    document.querySelector("[data-progression-comparison-form]"),
    document.querySelector("[data-progression-search-form]"),
  ].filter(Boolean);
  const overlay = document.getElementById("progression-comparison-overlay");
  if (!forms.length || !overlay) return;

  const workspace = document.querySelector(".staff-workspace");
  const sidebar = document.querySelector(".staff-sidebar");
  const title = document.getElementById("progression-processing-title");
  const message = document.getElementById("progression-processing-message");
  const submitButtons = forms.flatMap((form) => [
    ...form.querySelectorAll('button[type="submit"]'),
  ]);
  const workspaceContent = workspace
    ? [...workspace.children].filter((element) => element !== overlay)
    : [];
  let isProcessing = false;

  const setInert = (element, inert) => {
    if (element) element.inert = inert;
  };

  const reset = () => {
    isProcessing = false;
    document.body.classList.remove("progression-is-processing");
    document.body.removeAttribute("aria-busy");
    setInert(sidebar, false);
    workspaceContent.forEach((element) => setInert(element, false));
    submitButtons.forEach((button) => {
      button.disabled = false;
      button.removeAttribute("aria-disabled");
    });
    overlay.hidden = true;
  };

  forms.forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented) return;
      if (isProcessing) {
        event.preventDefault();
        return;
      }

      isProcessing = true;
      document.body.classList.add("progression-is-processing");
      document.body.setAttribute("aria-busy", "true");
      setInert(sidebar, true);
      workspaceContent.forEach((element) => setInert(element, true));
      submitButtons.forEach((button) => {
        button.disabled = true;
        button.setAttribute("aria-disabled", "true");
      });
      title.textContent = form.dataset.processingTitle || "Processing request…";
      message.textContent = form.dataset.processingMessage
        || "Please keep this page open until the request finishes.";
      overlay.hidden = false;
      window.requestAnimationFrame(() => overlay.focus());
    });
  });

  window.addEventListener("pageshow", reset);
});
