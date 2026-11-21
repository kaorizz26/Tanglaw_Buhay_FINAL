(function () {
  "use strict";

  const form = document.querySelector("[data-access-form]");
  if (!form) return;

  const boxes = Array.from(form.querySelectorAll("[data-permission]"));
  const byCode = new Map(boxes.map((box) => [box.dataset.permission, box]));
  const dependencies = {
    "reviews.view": ["assessments.view"],
    "reviews.manage": ["reviews.view"],
    "submissions.manage": ["submissions.view"]
  };

  function grantWithDependencies(code) {
    const box = byCode.get(code);
    if (box) box.checked = true;
    (dependencies[code] || []).forEach(grantWithDependencies);
  }

  function removeDependents(code) {
    Object.entries(dependencies).forEach(([dependent, required]) => {
      if (!required.includes(code)) return;
      const box = byCode.get(dependent);
      if (box) box.checked = false;
      removeDependents(dependent);
    });
  }

  function selectedCodes() {
    return boxes.filter((box) => box.checked).map((box) => box.dataset.permission).sort();
  }

  const presetButtons = Array.from(document.querySelectorAll("[data-permissions]"));
  function updatePressedPreset() {
    const selected = JSON.stringify(selectedCodes());
    presetButtons.forEach((button) => {
      const preset = JSON.stringify(JSON.parse(button.dataset.permissions).sort());
      button.setAttribute("aria-pressed", String(preset === selected));
    });
  }

  boxes.forEach((box) => {
    box.addEventListener("change", function () {
      if (box.checked) grantWithDependencies(box.dataset.permission);
      else removeDependents(box.dataset.permission);
      updatePressedPreset();
    });
  });

  presetButtons.forEach((button) => {
    button.addEventListener("click", function () {
      const selected = new Set(JSON.parse(button.dataset.permissions));
      boxes.forEach((box) => { box.checked = selected.has(box.dataset.permission); });
      grantWithDependencies("dashboard.view");
      updatePressedPreset();
    });
  });

  updatePressedPreset();
}());
