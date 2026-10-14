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
