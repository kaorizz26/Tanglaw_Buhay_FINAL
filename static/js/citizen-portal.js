// Prevent repeated actions while a Citizen Portal form is being submitted.
// File uploads keep their more specific validation and upload message.
const portalForms = document.querySelectorAll("form:not([data-upload-form])");
const processingNotice = document.querySelector("[data-portal-processing]");

function beginPortalProcessing(form, submitter) {
  document.body.classList.add("portal-is-processing");
  document.body.setAttribute("aria-busy", "true");
  form.setAttribute("aria-busy", "true");

  document.querySelectorAll("button").forEach((button) => {
    button.disabled = true;
  });
  document.querySelectorAll("a").forEach((link) => {
    link.setAttribute("aria-disabled", "true");
    link.dataset.portalTabindex = link.getAttribute("tabindex") || "";
    link.setAttribute("tabindex", "-1");
  });

  if (submitter) {
    submitter.dataset.portalLabel = submitter.textContent;
    submitter.textContent = submitter.dataset.loadingLabel || "Processing…";
  }
  if (processingNotice) processingNotice.hidden = false;
}

function restorePortalActions() {
  document.body.classList.remove("portal-is-processing");
  document.body.removeAttribute("aria-busy");
  document.querySelectorAll("form[aria-busy=true]").forEach((form) => {
    form.removeAttribute("aria-busy");
  });
  document.querySelectorAll("button").forEach((button) => {
    button.disabled = false;
    if (button.dataset.portalLabel) {
      button.textContent = button.dataset.portalLabel;
      delete button.dataset.portalLabel;
    }
  });
  document.querySelectorAll("a[data-portal-tabindex]").forEach((link) => {
    link.removeAttribute("aria-disabled");
    if (link.dataset.portalTabindex) link.setAttribute("tabindex", link.dataset.portalTabindex);
    else link.removeAttribute("tabindex");
    delete link.dataset.portalTabindex;
  });
  if (processingNotice) processingNotice.hidden = true;
}

portalForms.forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (event.defaultPrevented) return;
    beginPortalProcessing(form, event.submitter);
  });
});

// A page restored from the browser's back-forward cache must be interactive again.
window.addEventListener("pageshow", restorePortalActions);
