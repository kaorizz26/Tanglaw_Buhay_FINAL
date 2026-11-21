(() => {
  const form = document.getElementById("import-upload-form");
  const page = document.getElementById("import-upload-page");
  const overlay = document.getElementById("import-processing-overlay");
  const sidebar = document.querySelector(".staff-sidebar");
  const previewHost = document.getElementById("import-preview-host");
  const processingTitle = document.getElementById("import-processing-title");
  const processingMessage = document.getElementById("import-processing-message");
  const previewEndpoint = form?.getAttribute("action");
  let isProcessing = false;

  const setPageInert = (element, inert) => {
    if (!element) return;
    element.inert = inert;
  };

  const submitButtons = () => Array.from(
    document.querySelectorAll('button[type="submit"]'),
  ).filter((button) => button.form === form);

  const resetProcessingState = () => {
    isProcessing = false;
    form?.removeAttribute("aria-busy");
    page?.removeAttribute("aria-busy");
    setPageInert(page, false);
    setPageInert(sidebar, false);
    submitButtons().forEach((button) => {
      button.disabled = false;
      button.removeAttribute("aria-disabled");
    });
    form?.querySelector('input[data-import-action]')?.remove();
    if (overlay) overlay.hidden = true;
  };

  const startProcessing = (requestedAction) => {
    isProcessing = true;
    const actionInput = document.createElement("input");
    actionInput.type = "hidden";
    actionInput.name = "action";
    actionInput.value = requestedAction;
    actionInput.dataset.importAction = "true";
    form.append(actionInput);

    submitButtons().forEach((button) => {
      button.disabled = true;
      button.setAttribute("aria-disabled", "true");
    });
    form.setAttribute("aria-busy", "true");
    page?.setAttribute("aria-busy", "true");
    setPageInert(page, true);
    setPageInert(sidebar, true);

    if (processingTitle) {
      processingTitle.textContent = requestedAction === "store"
        ? "Checking and storing CSV files…"
        : "Checking CSV files…";
    }
    if (processingMessage) {
      processingMessage.textContent = requestedAction === "store"
        ? "Please keep this page open. The files are being validated and securely stored as an inactive batch."
        : "Please keep this page open. The files are being validated and no dataset will be stored.";
    }
    if (overlay) {
      overlay.hidden = false;
      window.requestAnimationFrame(() => overlay.focus());
    }
  };

  const showPreviewError = (message) => {
    if (!previewHost) return;
    previewHost.replaceChildren();
    const alert = document.createElement("div");
    alert.className = "import-page-alert import-page-alert-error import-preview-request-error";
    alert.setAttribute("role", "alert");
    alert.textContent = message;
    previewHost.append(alert);
    previewHost.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const showPreviewHtml = (html) => {
    previewHost.innerHTML = html;
    previewHost.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const requestPreview = async (formData) => {
    try {
      const response = await fetch(previewEndpoint, {
        method: "POST",
        body: formData,
        headers: { "X-Requested-With": "fetch", Accept: "application/json" },
      });
      const responseText = await response.text();
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) {
        const documentResponse = new DOMParser().parseFromString(
          responseText,
          "text/html",
        );
        const serverPreview = documentResponse.getElementById("import-preview-host");
        if (serverPreview?.innerHTML.trim()) {
          showPreviewHtml(serverPreview.innerHTML);
          return;
        }
        if (response.redirected) {
          showPreviewError("Your session may have expired. Sign in again, then retry the preview.");
        } else {
          showPreviewError(
            `The import service returned an unexpected response (HTTP ${response.status}). `
            + "Restart the application and try again. If it continues, check the application log.",
          );
        }
        return;
      }
      const payload = JSON.parse(responseText);
      if (typeof payload.preview_html !== "string") {
        showPreviewError("The import service returned an incomplete preview. Try again.");
        return;
      }
      showPreviewHtml(payload.preview_html);
    } catch (error) {
      showPreviewError("The file preview could not reach the server. Check your connection and try again.");
    } finally {
      resetProcessingState();
    }
  };

  const clearPreview = ({ clearFiles = false } = {}) => {
    if (!previewHost?.hasChildNodes()) return;
    previewHost.replaceChildren();
    if (clearFiles) {
      form.querySelectorAll('input[type="file"]').forEach((input) => {
        input.value = "";
      });
      form.querySelector('input[type="file"]')?.focus();
    }
  };

  form?.addEventListener("submit", (event) => {
    if (isProcessing) {
      event.preventDefault();
      return;
    }

    const requestedAction = event.submitter?.value === "store" ? "store" : "check";
    if (requestedAction === "check") event.preventDefault();
    const previewData = requestedAction === "check" ? new FormData(form) : null;
    previewData?.set("action", "check");
    startProcessing(requestedAction);
    if (requestedAction === "check") requestPreview(previewData);
  });

  previewHost?.addEventListener("click", (event) => {
    if (!event.target.closest("[data-change-import-files]")) return;
    clearPreview({ clearFiles: true });
  });

  window.addEventListener("pageshow", resetProcessingState);

  document.querySelectorAll('.import-field input[type="file"]').forEach((input) => {
    const error = document.getElementById(`${input.id.replace("-csv", "")}-size-error`);
    if (!error) return;

    input.addEventListener("change", () => {
      clearPreview();
      const file = input.files?.[0];
      const maximum = Number.parseInt(input.dataset.maxBytes || "0", 10);
      const tooLarge = Boolean(file && maximum && file.size > maximum);
      const requiredFilename = input.dataset.requiredFilename;
      let message = "";
      if (file && !file.name.toLowerCase().endsWith(".csv")) {
        message = `${requiredFilename} must be a CSV file.`;
      } else if (file && file.name !== requiredFilename) {
        message = `Use the exact filename ${requiredFilename}.`;
      } else if (tooLarge) {
        message = `${requiredFilename} exceeds the ${input.dataset.maxMb} MB file limit. Choose a smaller file.`;
      }

      input.setCustomValidity(message);
      error.textContent = message;
      error.hidden = !message;
    });
  });

  const yearSelect = document.getElementById("survey-year");
  const warning = document.getElementById("duplicate-year-warning");
  if (!yearSelect || !warning) return;

  const yearText = warning.querySelector("[data-duplicate-year]");
  const summaryText = warning.querySelector("[data-duplicate-summary]");

  const updateDuplicateWarning = () => {
    const option = yearSelect.options[yearSelect.selectedIndex];
    const count = Number.parseInt(option?.dataset.storedCount || "0", 10);
    const hasStoredBatch = count > 0;

    warning.hidden = !hasStoredBatch;
    if (!hasStoredBatch) return;
    yearText.textContent = option.value;
    summaryText.textContent = `${count} stored ${count === 1 ? "batch exists" : "batches exist"}`;
  };

  yearSelect.addEventListener("change", () => {
    clearPreview();
    updateDuplicateWarning();
  });
  updateDuplicateWarning();
})();
