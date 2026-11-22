(function () {
  "use strict";

  const dialog = document.querySelector("[data-info-dialog]");
  if (!dialog) return;

  const title = dialog.querySelector("[data-info-dialog-title]");
  const body = dialog.querySelector("[data-info-dialog-body]");
  const terms = dialog.querySelector("[data-info-dialog-terms]");
  const closeButton = dialog.querySelector("[data-info-dialog-close]");
  let openingButton = null;

  function closeDialog() {
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }

  function showTermList(value) {
    terms.replaceChildren();
    value.split("\n").forEach(function (line) {
      const separator = line.indexOf(" — ");
      if (separator === -1) return;

      const term = document.createElement("dt");
      const description = document.createElement("dd");
      term.textContent = line.slice(0, separator);
      description.textContent = line.slice(separator + 3);
      terms.append(term, description);
    });
  }

  document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-info-title]");
    if (!button) return;

    openingButton = button;
    title.textContent = button.dataset.infoTitle;
    body.textContent = button.dataset.infoBody;
    const usesTerms = button.dataset.infoFormat === "terms";
    dialog.classList.toggle("info-dialog-wide", usesTerms);
    body.hidden = usesTerms;
    terms.hidden = !usesTerms;
    if (usesTerms) showTermList(button.dataset.infoBody);

    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    closeButton.focus();
  });

  closeButton.addEventListener("click", closeDialog);

  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) closeDialog();
  });

  dialog.addEventListener("close", function () {
    if (openingButton && openingButton.isConnected) openingButton.focus();
    openingButton = null;
  });

  dialog.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && typeof dialog.close !== "function") {
      event.preventDefault();
      closeDialog();
      if (openingButton && openingButton.isConnected) openingButton.focus();
      openingButton = null;
    }
  });
})();
