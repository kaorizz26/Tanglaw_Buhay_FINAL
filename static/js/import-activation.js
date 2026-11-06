(() => {
  const dialog = document.getElementById("dataset-switch-dialog");
  if (!dialog || typeof dialog.showModal !== "function") return;

  const cancelButton = dialog.querySelector("[data-switch-cancel]");
  const confirmButton = dialog.querySelector("[data-switch-confirm]");
  let pendingForm = null;

  document.querySelectorAll(".import-candidate-switch-form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.activationConfirmed === "yes") {
        delete form.dataset.activationConfirmed;
        return;
      }

      event.preventDefault();
      pendingForm = form;
      const batchId = form.elements.target_batch_id.value;
      const card = form.closest(".import-candidate-card");
      const surveyYear = card?.querySelector(".import-batch-year")?.textContent
        .replace("Survey year", "").trim() || "the selected year";

      dialog.querySelectorAll("[data-switch-batch]").forEach((element) => {
        element.textContent = batchId;
      });
      dialog.querySelectorAll("[data-switch-year]").forEach((element) => {
        element.textContent = surveyYear;
      });
      dialog.showModal();
      cancelButton.focus();
    });
  });

  cancelButton.addEventListener("click", () => dialog.close());

  confirmButton.addEventListener("click", () => {
    if (!pendingForm) return;
    const form = pendingForm;
    form.dataset.activationConfirmed = "yes";
    dialog.close();
    form.requestSubmit();
  });

  dialog.addEventListener("close", () => {
    pendingForm = null;
  });
})();
