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

// Site > building > zone selects (templates/_location_fields.html), on forms and index filters.
// Every building and zone option has its parent's id as data-parent. Only the ones belonging to
// the selected site (or building) are shown, and a choice that no longer fits goes back to blank
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll('select[data-location="site"]').forEach((site) => {
    const scope = site.closest("form") || document;
    const building = scope.querySelector('select[data-location="building"]');
    const zone = scope.querySelector('select[data-location="zone"]');
    if (!building || !zone) return;

    const allOptions = new Map([
      [building, Array.from(building.options)],
      [zone, Array.from(zone.options)],
    ]);

    function narrow(select, parentId) {
      const current = select.value;
      // the blank option has no data-parent, so it's always kept
      const options = allOptions.get(select).filter((option) => !option.dataset.parent || option.dataset.parent === parentId);
      select.replaceChildren(...options);
      select.value = options.some((option) => option.value === current) ? current : "";
    }

    function update() {
      narrow(building, site.value);
      narrow(zone, building.value);
    }

    site.addEventListener("change", update);
    building.addEventListener("change", update);
    update();
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
