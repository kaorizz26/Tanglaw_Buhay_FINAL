"use strict";

const menuButton = document.querySelector(".navigation-toggle");
const navigation = document.querySelector("#staff-navigation");
const compactLayout = window.matchMedia("(max-width: 760px)");

function setNavigationExpanded(expanded) {
  navigation.hidden = !expanded;
  menuButton.setAttribute("aria-expanded", String(expanded));
  menuButton.textContent = expanded ? "Hide menu" : "Show menu";
}

function updateNavigationLayout() {
  menuButton.hidden = !compactLayout.matches;
  setNavigationExpanded(!compactLayout.matches);
}

if (menuButton && navigation) {
  menuButton.addEventListener("click", () => {
    const expanded = menuButton.getAttribute("aria-expanded") === "true";
    setNavigationExpanded(!expanded);
  });
  compactLayout.addEventListener("change", updateNavigationLayout);
  updateNavigationLayout();
}
