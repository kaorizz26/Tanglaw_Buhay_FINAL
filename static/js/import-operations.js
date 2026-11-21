(() => {
  const overlay = document.getElementById("import-operation-overlay");
  const page = document.querySelector("[data-import-operation-page]");
  const sidebar = document.querySelector(".staff-sidebar");
  const title = document.getElementById("import-operation-title");
  const message = document.getElementById("import-operation-message");
  const forms = Array.from(document.querySelectorAll("[data-import-operation-form]"));
  let isProcessing = false;

  if (!overlay || !page || !forms.length) return;

  const setInert = (element, inert) => {
    if (element) element.inert = inert;
  };

  const reset = () => {
    isProcessing = false;
    page.removeAttribute("aria-busy");
    setInert(page, false);
    setInert(sidebar, false);
    forms.forEach((form) => {
      form.querySelectorAll('button[type="submit"]').forEach((button) => {
        button.disabled = false;
        button.removeAttribute("aria-disabled");
      });
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
      page.setAttribute("aria-busy", "true");
      setInert(page, true);
      setInert(sidebar, true);
      forms.forEach((candidate) => {
        candidate.querySelectorAll('button[type="submit"]').forEach((button) => {
          button.disabled = true;
          button.setAttribute("aria-disabled", "true");
        });
      });
      title.textContent = form.dataset.processingTitle || "Processing request…";
      message.textContent = form.dataset.processingMessage
        || "Please keep this page open until the operation finishes.";
      overlay.hidden = false;
      window.requestAnimationFrame(() => overlay.focus());
    });
  });

  window.addEventListener("pageshow", reset);
})();
