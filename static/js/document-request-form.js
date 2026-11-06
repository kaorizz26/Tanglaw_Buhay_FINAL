/* Request Documents form: lets staff add more document rows (up to the allowed maximum). */
(function () {
  "use strict";

  var rowsContainer = document.getElementById("document-rows");
  var addButton = document.getElementById("add-document");
  if (!rowsContainer || !addButton) {
    return;
  }

  var maxRows = parseInt(rowsContainer.dataset.maxRows, 10);

  function rowCount() {
    return rowsContainer.querySelectorAll(".document-row").length;
  }

  function updateAddButton() {
    addButton.disabled = rowCount() >= maxRows;
  }

  /* Give a copied row a new number and clear its text, errors and ids. */
  function prepareNewRow(row, number) {
    row.querySelector(".document-row-number").textContent = "Document " + number;
    row.querySelectorAll(".account-field-error").forEach(function (message) {
      message.remove();
    });
    row.querySelectorAll("input, textarea").forEach(function (field) {
      var baseName = field.name === "document_title" ? "document_title_" : "document_instructions_";
      field.value = "";
      field.id = baseName + number;
      field.removeAttribute("aria-invalid");
      field.removeAttribute("aria-describedby");
    });
    row.querySelectorAll("label").forEach(function (label, index) {
      label.htmlFor = row.querySelectorAll("input, textarea")[index].id;
    });
  }

  addButton.hidden = false;
  addButton.addEventListener("click", function () {
    if (rowCount() >= maxRows) {
      return;
    }
    var newRow = rowsContainer.lastElementChild.cloneNode(true);
    prepareNewRow(newRow, rowCount() + 1);
    rowsContainer.appendChild(newRow);
    newRow.querySelector("input").focus();
    updateAddButton();
  });
  updateAddButton();
}());
