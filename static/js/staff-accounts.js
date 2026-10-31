(() => {
  const displayName = document.getElementById("display-name");
  const username = document.getElementById("staff-username");
  if (!displayName || !username) return;

  let editedUsername = Boolean(username.value);
  username.addEventListener("input", () => {
    username.value = username.value.toLowerCase();
    editedUsername = Boolean(username.value);
  });
  displayName.addEventListener("input", () => {
    if (editedUsername) return;
    username.value = displayName.value.normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, ".")
      .replace(/^\.+|\.+$/g, "")
      .slice(0, 64);
  });
})();

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const password = document.getElementById(button.dataset.copyTarget);
    const status = button.closest(".accounts-modal, dd").querySelector(".accounts-copy-status");
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(password.textContent);
      status.textContent = "Password copied";
    } catch {
      status.textContent = "Could not copy. Select the password and copy it manually.";
    }
  });
});

(() => {
  const dialog = document.getElementById("temporary-password-dialog");
  if (!dialog) return;

  // The open attribute leaves the details accessible if dialog scripting fails.
  if (typeof dialog.showModal === "function") {
    dialog.removeAttribute("open");
    dialog.showModal();
  }

  dialog.addEventListener("close", () => {
    const revealButton = document.querySelector(
      `.accounts-reveal[data-account-id="${dialog.dataset.accountId}"]`
    );
    dialog.remove();
    revealButton?.focus();
  });
})();
