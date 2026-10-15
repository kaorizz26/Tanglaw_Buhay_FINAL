"use strict";

for (const dimension of document.querySelectorAll(".mpi-dimension")) {
  dimension.addEventListener("toggle", () => {
    if (!dimension.open) {
      for (const evidence of dimension.querySelectorAll(
        ".cbms-evidence[open], .cbms-record-details[open]"
      )) {
        evidence.open = false;
      }
    }
  });
}

function openIndicatorFromHash() {
  const indicator = document.getElementById(window.location.hash.slice(1));
  if (!indicator || !indicator.matches(".mpi-indicator")) {
    return;
  }

  const selectedDimension = indicator.closest(".mpi-dimension");
  for (const dimension of document.querySelectorAll(".mpi-dimension")) {
    dimension.open = dimension === selectedDimension;
  }
  indicator.querySelector(".cbms-evidence").open = true;
  indicator.focus({ preventScroll: true });
  indicator.scrollIntoView({ block: "start" });
}

window.addEventListener("hashchange", openIndicatorFromHash);
for (const link of document.querySelectorAll(".mpi-indicator-link")) {
  link.addEventListener("click", () => {
    // A repeated link must reopen evidence even when the fragment is unchanged.
    if (link.hash === window.location.hash) {
      openIndicatorFromHash();
    }
  });
}
openIndicatorFromHash();
