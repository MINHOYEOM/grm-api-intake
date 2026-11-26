(function () {
  "use strict";

  const hangul = /[가-힣]/;
  const latinToken = /^[A-Za-z0-9/-]+$/;
  const escapeRegex = (text) => text.replace(/[.*+?^$(){}|[\]\\]/g, "\\$&");

  // Mirrors the renderer's Hangul spacing and word-start rules for the demo.
  function termPattern(surface, lang) {
    const literal = escapeRegex(surface);
    if (lang === "en" || latinToken.test(surface)) {
      return new RegExp("(?<![A-Za-z0-9])" + literal + "(?![A-Za-z0-9])", lang === "en" ? "gi" : "g");
    }
    if (!hangul.test(surface)) return new RegExp(literal, "g");
    const words = surface.trim().split(/\s+/);
    const tight = words.join("");
    const spaces = new Set();
    let offset = 0;
    for (const word of words.slice(0, -1)) {
      offset += word.length;
      spaces.add(offset);
    }
    const total = Array.from(tight).filter((char) => hangul.test(char)).length;
    let left = 0;
    let flexible = false;
    let pattern = "";
    for (let i = 0; i < tight.length; i++) {
      const char = tight[i];
      if (i && hangul.test(tight[i - 1]) && hangul.test(char) &&
          (spaces.has(i) || (left >= 2 && total - left >= 2))) {
        pattern += "[ \u00a0]?";
        flexible = true;
      } else if (spaces.has(i)) {
        pattern += " ";
      }
      pattern += escapeRegex(char);
      if (hangul.test(char)) left++;
    }
    return new RegExp(flexible ? pattern : literal, "g");
  }

  function termSpans(text, selected, lang, used = new Set()) {
    const spans = [];
    for (const { surface, id, pattern } of selected) {
      if (!surface || used.has(id)) continue;
      const checkHangul = lang === "ko" && hangul.test(surface);
      const checkVerbish = lang === "ko" && !latinToken.test(surface);
      pattern.lastIndex = 0;
      for (let match = pattern.exec(text); match; match = pattern.exec(text)) {
        const start = match.index;
        const end = start + match[0].length;
        const insideWord = checkHangul && start > 0 && hangul.test(text[start - 1]);
        const verbish = checkVerbish && (
          /(?:기|을|를)\s$/.test(text.slice(Math.max(0, start - 2), start)) ||
          /^(?:하|되|한|된|할|합|됩)/.test(text.slice(end, end + 2))
        );
        const overlaps = spans.some((span) => span.start < end && start < span.end);
        if (!insideWord && !verbish && !overlaps) {
          spans.push({ start, end, id });
          used.add(id);
          break;
        }
        pattern.lastIndex = start + 1;
      }
    }
    return spans.sort((a, b) => a.start - b.start);
  }

  function mountTermTooltips(root, dictionary) {
    let active;
    let pinned = false;
    let leaveTimer;
    const tooltips = new Map();

    function position() {
      if (!active) return;
      const tooltip = tooltips.get(active);
      const anchor = active.getBoundingClientRect();
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

    function hide() {
      clearTimeout(leaveTimer);
      if (active) tooltips.get(active).hidden = true;
      active = undefined;
      pinned = false;
    }

    function show(trigger, pin = false) {
      clearTimeout(leaveTimer);
      if (active !== trigger) hide();
      active = trigger;
      pinned ||= pin;
      tooltips.get(trigger).hidden = false;
      position();
    }

    function queueHide(trigger) {
      clearTimeout(leaveTimer);
      leaveTimer = setTimeout(() => {
        if (active === trigger && !pinned && document.activeElement !== trigger) hide();
      }, 180);
    }

    function triggerFor(label, definition, lang) {
      const trigger = document.createElement("button");
      trigger.type = "button";
      trigger.className = "term-trigger";
      trigger.textContent = label;

      const tooltip = document.createElement("span");
      tooltip.id = "clause-term-definition-" + (tooltips.size + 1);
      tooltip.className = "term-tooltip";
      tooltip.lang = lang;
      tooltip.setAttribute("role", "tooltip");
      tooltip.textContent = definition;
      tooltip.hidden = true;
      trigger.setAttribute("aria-describedby", tooltip.id);
      tooltips.set(trigger, tooltip);
      document.body.append(tooltip);

      trigger.addEventListener("focus", () => show(trigger));
      trigger.addEventListener("blur", () => {
        if (active === trigger) hide();
      });
      trigger.addEventListener("pointerenter", (event) => {
        if (event.pointerType === "mouse" && !pinned) show(trigger);
      });
      trigger.addEventListener("pointerleave", () => queueHide(trigger));
      trigger.addEventListener("click", () => {
        if (active === trigger && pinned) hide();
        else show(trigger, true);
      });
      tooltip.addEventListener("pointerenter", () => clearTimeout(leaveTimer));
      tooltip.addEventListener("pointerleave", () => queueHide(trigger));
      return trigger;
    }

    for (const lang of ["ko", "en"]) {
      const selected = dictionary.filter((term) => term.lang === lang)
        .map((term) => ({ ...term, pattern: termPattern(term.surface, lang) }));
      const definitions = new Map(selected.map(({ id, definition }) => [id, definition]));
      const used = new Set();
      for (const paragraph of root.querySelectorAll("[data-term-lang=" + lang + "] [data-find-block]")) {
        // The source clause currently renders each paragraph as plain text.
        if (paragraph.childNodes.length !== 1 || paragraph.firstChild.nodeType !== Node.TEXT_NODE) continue;
        const text = paragraph.textContent;
        const spans = termSpans(text, selected, lang, used);
        if (!spans.length) continue;
        const fragment = document.createDocumentFragment();
        let cursor = 0;
        for (const { start, end, id } of spans) {
          fragment.append(text.slice(cursor, start));
          fragment.append(triggerFor(text.slice(start, end), definitions.get(id), lang));
          cursor = end;
        }
        fragment.append(text.slice(cursor));
        paragraph.replaceChildren(fragment);
      }
    }

    document.addEventListener("pointerdown", (event) => {
      if (active && !active.contains(event.target) && !tooltips.get(active).contains(event.target)) hide();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && active) hide();
    });
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, { passive: true });
  }

  if (typeof module === "object") {
    module.exports = { termPattern, termSpans };
  } else {
    window.ClauseReadabilityTerms = mountTermTooltips;
  }
})();
