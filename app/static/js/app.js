// Auto-hide toasts after 5 seconds
document.addEventListener("DOMContentLoaded", function () {
  const toasts = document.querySelectorAll(".toast");
  toasts.forEach((toast) => {
    const bsToast = new bootstrap.Toast(toast);
    bsToast.show();

    // Auto-hide after 5 seconds (5000ms)
    setTimeout(() => {
      bsToast.hide();
    }, 5000);
  });
});

// Person forms: only show the user group select when "Can log in" is ticked
document.addEventListener("DOMContentLoaded", function () {
  const canLogin = document.getElementById("can_login");
  const groupField = document.getElementById("user-group-field");
  if (!canLogin || !groupField) return;

  canLogin.addEventListener("change", () => {
    groupField.classList.toggle("d-none", !canLogin.checked);
  });
});

// Searchable selects: every .form-select becomes a Tom Select (from the CDN in base.html), so you can
// type to find a location, person, category... The native select stays in the form and is what's
// submitted. Add data-no-search to a select to leave it plain.
// Every select types its search into a box at the top of the dropdown (dropdown_input), so what you type
// never sits next to the current choices in the field. A multiple select also gets a remove button on each chosen item
document.addEventListener("DOMContentLoaded", function () {
  if (typeof TomSelect === "undefined") return; // the CDN didn't load: plain selects still work
  document.querySelectorAll("select.form-select:not([data-no-search])").forEach((select) => {
    new TomSelect(select, {
      // the blank option ("-", "All locations") is a real choice
      allowEmptyOption: true,
      // show every option, not just the first 50
      maxOptions: null,
      create: false,
      plugins: select.multiple ? ["remove_button", "dropdown_input"] : ["dropdown_input"],
    });
  });
});

// Table Columns menus (see templates/_table_tools.html). A table with data-columns="<key>" gets a
// checkbox for each header with data-col. Hidden columns are remembered per key in this browser,
// so a choice on the jobs index also applies to jobs tabs. Downloads get the visible columns
document.addEventListener("DOMContentLoaded", function () {
  function readHidden(storageKey) {
    try {
      return JSON.parse(localStorage.getItem(storageKey)) || [];
    } catch (error) {
      return [];
    }
  }

  function saveHidden(storageKey, hidden) {
    try {
      localStorage.setItem(storageKey, JSON.stringify(Array.from(hidden)));
    } catch (error) {
      // storage blocked (private window): the choice just isn't remembered
    }
  }

  document.querySelectorAll("table[data-columns]").forEach((table) => {
    const key = table.dataset.columns;
    const storageKey = "hidden-columns:" + key;
    const headers = Array.from(table.querySelectorAll("thead th"));
    const hideable = headers.filter((th) => th.dataset.col && !("colFixed" in th.dataset));
    const hidden = new Set(readHidden(storageKey));

    function apply() {
      headers.forEach((th, index) => {
        const hide = hideable.includes(th) && hidden.has(th.dataset.col);
        table.querySelectorAll(`tr > :nth-child(${index + 1})`).forEach((cell) => {
          cell.classList.toggle("d-none", hide);
        });
      });
    }

    const menu = document.querySelector(`[data-columns-menu="${key}"]`);
    if (menu) {
      hideable.forEach((th) => {
        const id = `columns-${key}-${th.dataset.col}`;
        const item = document.createElement("div");
        item.className = "form-check text-nowrap";

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "form-check-input";
        checkbox.id = id;
        checkbox.checked = !hidden.has(th.dataset.col);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) {
            hidden.delete(th.dataset.col);
          } else {
            hidden.add(th.dataset.col);
          }
          saveHidden(storageKey, hidden);
          apply();
        });

        const label = document.createElement("label");
        label.className = "form-check-label";
        label.htmlFor = id;
        label.textContent = th.textContent.trim();

        item.append(checkbox, label);
        menu.append(item);
      });
    }

    // set the columns as the link is clicked, so it always matches the current choice
    document.querySelectorAll(`[data-export-for="${key}"]`).forEach((link) => {
      link.addEventListener("click", () => {
        const visible = headers
          .filter((th) => th.dataset.col && !(hideable.includes(th) && hidden.has(th.dataset.col)))
          .map((th) => th.dataset.col);
        const url = new URL(link.href, window.location.href);
        url.searchParams.set("columns", visible.join(","));
        link.href = url.toString();
      });
    });

    apply();
  });
});

// Actions on selected rows (jobs/job_list.html): a form with data-row-actions, whose rows' checkboxes name it in their
// form attribute. The button needs something ticked. A record has a checkbox in the desktop table and another
// in the mobile cards, and both are in the form, so they're kept the same: otherwise turning a tablet round
// could hide ticks that would still be submitted
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("form[data-row-actions]").forEach((form) => {
    const boxes = Array.from(document.querySelectorAll(`input[type="checkbox"][form="${form.id}"]`));
    const button = form.querySelector("[data-row-actions-button]");
    const selectAll = document.querySelector(`[data-select-all="${form.id}"]`);

    function refresh() {
      const ticked = boxes.filter((box) => box.checked).length;
      button.disabled = ticked === 0;
      selectAll.checked = ticked === boxes.length;
      selectAll.indeterminate = ticked > 0 && ticked < boxes.length;
    }

    boxes.forEach((box) => {
      box.addEventListener("change", () => {
        boxes.filter((other) => other.value === box.value).forEach((other) => {
          other.checked = box.checked;
        });
        refresh();
      });
    });

    selectAll.addEventListener("change", () => {
      boxes.forEach((box) => {
        box.checked = selectAll.checked;
      });
      refresh();
    });

    // the browser can bring ticks back after a reload or the back button
    refresh();
    window.addEventListener("pageshow", refresh);
  });
});

// Slow actions cover the page with #busy-overlay (base.html), which blocks every button until they finish.
// - a form with data-busy="<message>": shown on submit, and the next page replaces it
// - a link with data-busy="<message>", or a form that also has data-busy-download, gets a file. The page never
//   changes for a download, so it's fetched here to know when it has finished. Anything that isn't a download
//   (an error, or a redirect to sign in) is done again the normal way, so the browser shows that page
document.addEventListener("DOMContentLoaded", function () {
  const overlay = document.getElementById("busy-overlay");
  if (!overlay) return;
  const message = document.getElementById("busy-message");
  const pageContent = Array.from(document.body.children).filter((element) => element !== overlay);

  function setBusy(busy, text) {
    if (busy) message.textContent = text;
    overlay.classList.toggle("d-none", !busy);
    // inert, not disabled, so a form still submits all its fields. It stops the keyboard reaching the page too
    pageContent.forEach((element) => {
      element.inert = busy;
    });
  }

  function saveFile(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  async function download(url, options, text, doItNormally) {
    setBusy(true, text);
    try {
      const response = await fetch(url, options);
      const filename = (response.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/);
      if (response.ok && filename) {
        saveFile(await response.blob(), filename[1]);
        setBusy(false);
        return;
      }
    } catch (error) {
      // the connection failed: the browser shows its own error below
    }
    setBusy(false);
    doItNormally();
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!form.dataset.busy) return;
    if ("busyDownload" in form.dataset) {
      event.preventDefault();
      download(form.action, { method: "POST", body: new FormData(form) }, form.dataset.busy, () => form.submit());
    } else {
      setBusy(true, form.dataset.busy);
    }
  });

  // on the document, so a table's download link has already had its columns added
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-busy]");
    if (!link) return;
    event.preventDefault();
    download(link.href, {}, link.dataset.busy, () => {
      window.location.href = link.href;
    });
  });

  // the back button can bring back this page as it was left, overlay included
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) setBusy(false);
  });
});

// Cancel buttons with data-close-window (the final delete confirmation, opened from an email link):
// close the tab. Browsers only allow that for some tabs, so if it's still open, go to the fallback url
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("[data-close-window]").forEach((button) => {
    button.addEventListener("click", () => {
      window.close();
      setTimeout(() => {
        window.location.href = button.dataset.closeWindow;
      }, 300);
    });
  });
});
