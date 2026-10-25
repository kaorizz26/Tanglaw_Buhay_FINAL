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
