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

const assessmentEntries = document.querySelectorAll("[data-assessment-entry]");
const assessmentOverlay = document.getElementById("assessment-entry-overlay");
const assessmentKicker = document.getElementById("assessment-loading-kicker");
const assessmentTitle = document.getElementById("assessment-loading-title");
const assessmentMessage = document.getElementById("assessment-loading-message");

if (assessmentEntries.length && assessmentOverlay) {
  const resetAssessmentEntry = () => {
    document.body.classList.remove("portal-is-loading");
    document.body.removeAttribute("aria-busy");
    assessmentOverlay.hidden = true;
  };

  assessmentEntries.forEach((entry) => {
    entry.addEventListener("click", (event) => {
      if (event.defaultPrevented || event.button !== 0
          || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      assessmentKicker.textContent = entry.textContent.trim();
      assessmentTitle.textContent = entry.dataset.loadingTitle;
      assessmentMessage.textContent = entry.dataset.loadingMessage;
      document.body.classList.add("portal-is-loading");
      document.body.setAttribute("aria-busy", "true");
      assessmentOverlay.hidden = false;
      window.requestAnimationFrame(() => assessmentOverlay.focus());
    });
  });

  window.addEventListener("pageshow", resetAssessmentEntry);
}
