// Convenience only: warns about a wrong or large file before sending and stops double clicks.
// The server checks the file again and is the real protection.
const form = document.querySelector("[data-upload-form]");
if (form) {
  const input = form.querySelector("input[type=file]");
  const button = form.querySelector("[data-upload-button]");
  const message = form.querySelector("[data-upload-message]");
  const maxBytes = Number(form.dataset.maxBytes);
  const allowed = /\.(pdf|jpe?g|png)$/i;
  const buttonText = button.textContent;

  function problemWith(file) {
    if (!allowed.test(file.name)) return "Choose a PDF, JPG or PNG file.";
    if (file.size > maxBytes) return "The file is larger than 5 MB. Choose a smaller file.";
    if (file.size === 0) return "The file is empty. Choose a different file.";
    return "";
  }

  function showProblem() {
    const text = input.files.length ? problemWith(input.files[0]) : "";
    message.textContent = text;
    message.hidden = !text;
    input.setAttribute("aria-invalid", String(Boolean(text)));
    return text;
  }

  input.addEventListener("change", showProblem);

  form.addEventListener("submit", (event) => {
    if (showProblem()) {
      event.preventDefault();
      return;
    }
    button.disabled = true;
    button.textContent = "Uploading…";
  });

  // Coming back with the Back button should not leave the button stuck.
  window.addEventListener("pageshow", () => {
    button.disabled = false;
    button.textContent = buttonText;
  });
}
