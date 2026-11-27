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

  // A length-preserving fold keeps match offsets in the original DOM text.
  function fold(text) {
    return text.replace(/[A-Z]/g, (letter) => letter.toLowerCase());
  }

  function normalizeQuery(query) {
    return fold(query.trim());
  }

  function findSpans(foldedText, query) {
    if (!query) return [];
    const spans = [];
    const startsInHangul = /^[가-힣]/.test(query);
    let start = foldedText.indexOf(query);
    while (start !== -1) {
      if (!startsInHangul || start === 0 || !/[가-힣]/.test(foldedText[start - 1])) {
        spans.push([start, start + query.length]);
      }
      start = foldedText.indexOf(query, start + 1);
    }
    return spans;
  }

  function stepResult(current, total, direction) {
    if (!total) return -1;
    if (current === -1) return direction === 1 ? 0 : total - 1;
    return (current + direction + total) % total;
  }

  function indexBlock(block) {
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return node.length && !node.parentElement.closest("[data-find-ignore], [hidden], script, style")
          ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    const parts = [];
    let offset = 0;
    while (walker.nextNode()) {
      const node = walker.currentNode;
      parts.push({ node, start: offset, end: offset + node.length });
      offset += node.length;
    }
    return { parts, text: fold(parts.map(({ node }) => node.data).join("")) };
  }

  function rangeFor(parts, start, end) {
    const first = parts.find((part) => part.end > start);
    const last = parts.find((part) => part.end >= end);
    const range = document.createRange();
    range.setStart(first.node, start - first.start);
    range.setEnd(last.node, end - last.start);
    return range;
  }

  function mountFind(form) {
    if (!globalThis.CSS?.highlights || typeof Highlight !== "function") {
      form.querySelector("[data-find-unavailable]").hidden = false;
      form.hidden = false;
      return;
    }

    const input = form.elements.query;
    const previous = form.querySelector("[data-find-prev]");
    const next = form.querySelector("[data-find-next]");
    const status = form.querySelector("output");
    const index = Array.from(form.parentElement.querySelectorAll("[data-find-block]"), indexBlock);
    let results = [];
    let current = -1;
    let pending;

    function showCurrent(scroll) {
      if (current === -1) {
        CSS.highlights.delete("clause-find-current");
      } else {
        const range = results[current];
        const highlight = new Highlight(range);
        highlight.priority = 1;
        CSS.highlights.set("clause-find-current", highlight);
        if (scroll) {
          const rect = range.getBoundingClientRect();
          const toolbarBottom = form.getBoundingClientRect().bottom;
          const target = toolbarBottom + Math.max(0, window.innerHeight - toolbarBottom) / 3;
          window.scrollBy({ top: rect.top - target, behavior: "instant" });
        }
      }
      previous.disabled = next.disabled = !results.length;
      status.textContent = _t("{total}개 중 {current}번째", { current: current + 1, total: results.length });
    }

    function refresh() {
      clearTimeout(pending);
      pending = undefined;
      const query = normalizeQuery(input.value);
      results = index.flatMap(({ parts, text }) => (
        findSpans(text, query).map(([start, end]) => rangeFor(parts, start, end))
      ));
      const highlight = new Highlight();
      for (const range of results) highlight.add(range);
      CSS.highlights.set("clause-find-all", highlight);
      current = stepResult(-1, results.length, 1);
      showCurrent(true);
    }

    function move(direction) {
      if (pending !== undefined) {
        refresh();
        if (direction === 1) return;
      }
      current = stepResult(current, results.length, direction);
      showCurrent(true);
    }

    input.addEventListener("input", (event) => {
      if (event.isComposing) return;
      clearTimeout(pending);
      pending = setTimeout(refresh, 100);
    });
    input.addEventListener("compositionstart", () => {
      clearTimeout(pending);
      pending = undefined;
    });
    input.addEventListener("compositionend", refresh);
    input.addEventListener("keydown", (event) => {
      if (event.isComposing || event.keyCode === 229 || event.key !== "Enter") return;
      event.preventDefault();
      move(event.shiftKey ? -1 : 1);
    });
    form.addEventListener("submit", (event) => event.preventDefault());
    previous.addEventListener("click", () => move(-1));
    next.addEventListener("click", () => move(1));
    form.querySelector("[data-find-controls]").hidden = false;
    form.hidden = false;
  }

  if (typeof module === "object") {
    module.exports = { fold, normalizeQuery, findSpans, stepResult };
  } else {
    const form = document.getElementById("clause-find");
    if (form) mountFind(form);
  }
})();
