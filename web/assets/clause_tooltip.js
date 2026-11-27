(function () {
  "use strict";

  var _t = function (s, v) {
    var d = window.GRM_I18N, r = (d && Object.prototype.hasOwnProperty.call(d, s)) ? d[s] : s;
    return v ? r.replace(/\{(\w+)\}/g, function (m, k) {
      if (k === "s" && !Object.prototype.hasOwnProperty.call(v, "s")) {
        var pn = v.n;
        return (pn === 1 || pn === "1") ? "" : "s";
      }
      return Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }) : r;
  };

  let active;
  let pinned = false;
  let leaveTimer;

  function hide() {
    clearTimeout(leaveTimer);
    if (active) active.tooltip.hidden = true;
    active = undefined;
    pinned = false;
  }

  function position() {
    if (!active) return;
    const { button, tooltip } = active;
    const anchor = button.getBoundingClientRect();
    if (anchor.bottom < 0 || anchor.top > window.innerHeight) {
      hide();
      return;
    }
    const box = tooltip.getBoundingClientRect();
    const rightEdge = document.documentElement.clientWidth - box.width - 8;
    const left = Math.max(8, Math.min(anchor.left, rightEdge));
    const below = anchor.bottom + 6;
    const above = anchor.top - box.height - 6;
    const top = below + box.height <= window.innerHeight - 8 ? below : Math.max(8, above);
    tooltip.style.left = left + "px";
    tooltip.style.top = top + "px";
  }

  function show(term, pin = false) {
    if (active !== term) hide();
    clearTimeout(leaveTimer);
    active = term;
    pinned ||= pin;
    term.tooltip.hidden = false;
    position();
  }

  function queueHide(term) {
    clearTimeout(leaveTimer);
    leaveTimer = setTimeout(() => {
      if (active === term && !pinned && document.activeElement !== term.button) hide();
    }, 180);
  }

  function enhance(link, index) {
    const button = document.createElement("button");
    const tooltip = document.createElement("span");
    const term = { button, tooltip };
    tooltip.id = "clause-term-definition-" + (index + 1);
    tooltip.className = "ct-tool-tip";
    tooltip.lang = link.closest("[lang]").lang;
    tooltip.setAttribute("role", "tooltip");
    tooltip.textContent = link.dataset.clauseDefinition;
    tooltip.hidden = true;
    document.body.append(tooltip);

    button.type = "button";
    button.className = "ct-term";
    button.textContent = link.textContent;
    button.setAttribute("aria-describedby", tooltip.id);
    button.addEventListener("focus", () => show(term));
    button.addEventListener("blur", () => {
      if (active === term) hide();
    });
    button.addEventListener("pointerenter", (event) => {
      if (event.pointerType === "mouse" && !pinned) show(term);
    });
    button.addEventListener("pointerleave", () => queueHide(term));
    button.addEventListener("click", () => {
      if (active === term && pinned) hide();
      else show(term, true);
    });
    tooltip.addEventListener("pointerenter", () => clearTimeout(leaveTimer));
    tooltip.addEventListener("pointerleave", () => queueHide(term));
    link.replaceWith(button);
  }

  const links = document.querySelectorAll(".ct-text a[data-clause-definition]");
  if (!links.length) return;
  links.forEach(enhance);
  document.addEventListener("pointerdown", (event) => {
    if (active && !active.button.contains(event.target) && !active.tooltip.contains(event.target)) hide();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && active) hide();
  });
  window.addEventListener("resize", position);
  window.addEventListener("scroll", position, { passive: true });
})();
