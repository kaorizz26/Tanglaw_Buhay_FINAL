// Adds an accessible show/hide control to each password field wrapper.
document.querySelectorAll("[data-password-field]").forEach((wrapper) => {
  const input = wrapper.querySelector("input");
  const button = wrapper.querySelector("[data-password-toggle]");
  if (!input || !button) return;
  button.hidden = false;
  button.addEventListener("click", () => {
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    button.setAttribute("aria-pressed", String(show));
    button.textContent = show ? "Hide" : "Show";
    button.setAttribute("aria-label", (show ? "Hide " : "Show ") + button.dataset.label);
  });
});
