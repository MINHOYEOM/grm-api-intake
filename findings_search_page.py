#!/usr/bin/env python3
"""FIND-1 M2c offline single-file HTML search viewer builder.

This module reads a `grm-findings-search/v1` envelope (as produced by
`findings_search_export.py`) and renders one self-contained, offline HTML
document: a vanilla-JS keyword + facet search viewer over the embedded
findings records. It performs no network calls, no SQLite access, and no
Notion/Supabase access -- the only input is the export dict already loaded
in memory (or read from disk by the CLI).

Determinism contract: the same export dict must always produce the exact
same HTML string, byte for byte. There is no use of `datetime.now()`,
`random`, environment variables, or filesystem/timezone state anywhere in
`build_search_page`.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

import grm_findings as gf


SEARCH_PAGE_SCHEMA_VERSION = "grm-findings-search-page/v1"

# FIND-1 M6d: 카테고리 드롭다운 옵션의 label_en 은 grm_findings.FINDING_TAXONOMY 에서
# 직접 가져온다(하드코딩 금지) -- 20개 code -> (label_ko, label_en).
_CATEGORY_TAXONOMY_LABELS: dict[str, tuple[str, str]] = {
    c.code: (c.label_ko, c.label_en) for c in gf.FINDING_TAXONOMY
}

# Schema version this builder accepts as input (produced by findings_search_export.py).
_REQUIRED_EXPORT_SCHEMA_VERSION = "grm-findings-search/v1"

# (facet key in export["facets"], <select> element id, Korean label)
_FILTER_SPECS: tuple[tuple[str, str, str], ...] = (
    ("agency", "filter-agency", "기관"),
    ("category_code", "filter-category", "카테고리"),
    ("source", "filter-source", "소스"),
    ("evidence_level", "filter-evidence", "증거 수준"),
    ("review_status", "filter-review", "검토 상태"),
    ("published_month", "filter-month", "발행월"),
)


_APP_CSS = """
:root {
  color-scheme: light;
  --bg: #f7f7f5;
  --panel: #ffffff;
  --border: #dcdcd6;
  --text: #1f2320;
  --text-muted: #5b615c;
  --accent: #2f5d3a;
  --badge-bg: #eef1ec;
  --tag-bg: #f0ede2;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  line-height: 1.5;
}

.app-header {
  padding: 20px 24px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
}

.app-header h1 {
  margin: 0 0 6px 0;
  font-size: 1.4rem;
}

.app-meta {
  color: var(--text-muted);
  font-size: 0.9rem;
}

.app-disclosure {
  margin-top: 8px;
  color: var(--text-muted);
  font-size: 0.8rem;
}

.app-main {
  max-width: 960px;
  margin: 0 auto;
  padding: 20px 24px 60px 24px;
}

.controls {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}

.search-row {
  margin-bottom: 12px;
}

#search-input {
  width: 100%;
  padding: 10px 12px;
  font-size: 1rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--text);
}

.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: end;
}

.filter-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.8rem;
  color: var(--text-muted);
}

.filter-field select {
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--panel);
  color: var(--text);
  font-size: 0.9rem;
  max-width: 220px;
}

#reset-btn {
  padding: 7px 14px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--panel);
  color: var(--text);
  cursor: pointer;
  font-size: 0.9rem;
  height: fit-content;
}

#reset-btn:hover {
  background: var(--badge-bg);
}

.result-count {
  margin: 4px 0 12px 2px;
  color: var(--text-muted);
  font-size: 0.9rem;
}

.result-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.finding-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
}

.card-head {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--badge-bg);
  font-size: 0.75rem;
  color: var(--text);
}

.card-firm {
  font-weight: 600;
  margin-bottom: 2px;
}

.card-category {
  color: var(--accent);
  font-size: 0.85rem;
  margin-bottom: 6px;
}

.card-subtitle {
  color: var(--text-muted);
  font-size: 0.85rem;
  margin-bottom: 6px;
}

.card-text {
  white-space: pre-wrap;
  margin: 0 0 8px 0;
}

.card-orig {
  margin: 0 0 8px 0;
}

.card-orig summary {
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--accent);
}

.card-orig p {
  white-space: pre-wrap;
  font-size: 0.85rem;
  color: var(--text-muted);
  margin: 6px 0 0 0;
}

.card-tr-note {
  display: block;
  font-size: 0.75rem;
  color: var(--text-muted);
  margin: 0 0 8px 0;
}

.card-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.tag {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 4px;
  background: var(--tag-bg);
  font-size: 0.75rem;
  color: var(--text-muted);
}

.card-link a {
  color: var(--accent);
  font-size: 0.85rem;
}

.card-link span {
  color: var(--text-muted);
  font-size: 0.85rem;
}

.empty-message {
  color: var(--text-muted);
  padding: 24px 0;
  text-align: center;
}
"""


_APP_JS = """
(function () {
  "use strict";

  var dataEl = document.getElementById("findings-data");
  var records = JSON.parse(dataEl.textContent || "[]");
  var totalCount = records.length;

  var searchInput = document.getElementById("search-input");
  var filterIds = {
    agency: "filter-agency",
    category_code: "filter-category",
    source: "filter-source",
    evidence_level: "filter-evidence",
    review_status: "filter-review",
    published_month: "filter-month"
  };
  var resetBtn = document.getElementById("reset-btn");
  var resultCountEl = document.getElementById("result-count");
  var resultListEl = document.getElementById("result-list");
  var emptyMessageEl = document.getElementById("empty-message");

  var debounceTimer = null;

  function publishedMonth(record) {
    var d = String(record.published_date || "");
    return d.length >= 7 ? d.slice(0, 7) : "";
  }

  function matchesFilters(record) {
    var keyword = (searchInput.value || "").trim().toLowerCase();
    if (keyword) {
      var haystack = [
        record.finding_text || "",
        record.firm_name || "",
        record.document_id || ""
      ].join(" \\n ").toLowerCase();
      if (haystack.indexOf(keyword) === -1) {
        return false;
      }
    }
    for (var field in filterIds) {
      if (!Object.prototype.hasOwnProperty.call(filterIds, field)) {
        continue;
      }
      var el = document.getElementById(filterIds[field]);
      var val = el.value;
      if (!val) {
        continue;
      }
      var recordVal = field === "published_month" ? publishedMonth(record) : String(record[field] || "");
      if (recordVal !== val) {
        return false;
      }
    }
    return true;
  }

  function clearChildren(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function appendTag(parent, tag, text, className) {
    var el = document.createElement(tag);
    if (className) {
      el.className = className;
    }
    if (text !== undefined && text !== null && text !== "") {
      el.textContent = text;
    }
    parent.appendChild(el);
    return el;
  }

  function buildEvidenceLink(container, url, label) {
    if (typeof url === "string" && (url.indexOf("http://") === 0 || url.indexOf("https://") === 0)) {
      var a = document.createElement("a");
      a.href = url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = label;
      container.appendChild(a);
    } else {
      appendTag(container, "span", label);
    }
  }

  function appendFindingText(card, record) {
    // [원문·국문 병기 M6d] finding_text_ko 가 있으면 국문을 본문으로, 원문은 접기(details)로
    // 낮춰 보여준다. 없으면 기존처럼 원문만 그대로 표시.
    var ko = String(record.finding_text_ko || "").trim();
    if (!ko) {
      appendTag(card, "p", record.finding_text || "", "card-text");
      return;
    }
    appendTag(card, "p", ko, "card-text");
    if (record.finding_text) {
      var details = document.createElement("details");
      details.className = "card-orig";
      var summary = document.createElement("summary");
      summary.textContent = "원문 보기 (영문)";
      details.appendChild(summary);
      var p = document.createElement("p");
      p.textContent = record.finding_text;
      details.appendChild(p);
      card.appendChild(details);
    }
    if (record.translation_method === "llm_assisted") {
      appendTag(card, "span", "AI 번역 — 원문 대조 권장", "card-tr-note");
    }
  }

  function renderCard(record) {
    var card = document.createElement("article");
    card.className = "finding-card";

    var head = appendTag(card, "div", null, "card-head");
    appendTag(head, "span", record.published_date || "", "badge badge-date");
    appendTag(head, "span", record.agency || "", "badge badge-agency");
    appendTag(head, "span", record.source || "", "badge badge-source");
    appendTag(head, "span", record.evidence_level || "", "badge badge-evidence");
    appendTag(head, "span", record.review_status || "", "badge badge-review");

    appendTag(card, "div", record.firm_name || "", "card-firm");
    appendTag(card, "div", record.category_label_ko || record.category_code || "", "card-category");

    var rawSignal = record.raw_signal;
    if (rawSignal && rawSignal.title) {
      appendTag(card, "div", rawSignal.title, "card-subtitle");
    }

    appendFindingText(card, record);

    var refs = [];
    (record.cfr_refs || []).forEach(function (ref) {
      refs.push(ref);
    });
    (record.mfds_refs || []).forEach(function (ref) {
      refs.push(ref);
    });
    if (refs.length > 0) {
      var tagWrap = appendTag(card, "div", null, "card-tags");
      refs.forEach(function (ref) {
        appendTag(tagWrap, "span", ref, "tag");
      });
    }

    var linkWrap = appendTag(card, "div", null, "card-link");
    buildEvidenceLink(linkWrap, record.evidence_url, "원문 보기");

    return card;
  }

  function render() {
    var filtered = records.filter(matchesFilters);
    resultCountEl.textContent = filtered.length + "건 / 전체 " + totalCount + "건";
    clearChildren(resultListEl);
    if (filtered.length === 0) {
      emptyMessageEl.hidden = false;
    } else {
      emptyMessageEl.hidden = true;
      filtered.forEach(function (record) {
        resultListEl.appendChild(renderCard(record));
      });
    }
  }

  searchInput.addEventListener("input", function () {
    if (debounceTimer) {
      clearTimeout(debounceTimer);
    }
    debounceTimer = setTimeout(render, 150);
  });

  Object.keys(filterIds).forEach(function (field) {
    document.getElementById(filterIds[field]).addEventListener("change", render);
  });

  resetBtn.addEventListener("click", function () {
    searchInput.value = "";
    Object.keys(filterIds).forEach(function (field) {
      document.getElementById(filterIds[field]).value = "";
    });
    render();
  });

  render();
})();
"""


def _category_label_map(records: list[dict[str, Any]]) -> dict[str, str]:
    """Map category_code -> category_label_ko using the export's own records.

    Built by a single deterministic left-to-right scan of `records` (which is
    already sorted deterministically upstream); first label seen per code wins.
    """
    labels: dict[str, str] = {}
    for record in records:
        code = str(record.get("category_code") or "")
        if code and code not in labels:
            labels[code] = str(record.get("category_label_ko") or "")
    return labels


def _option_label(field: str, value: str, category_labels: dict[str, str]) -> str:
    if field == "category_code":
        taxonomy = _CATEGORY_TAXONOMY_LABELS.get(value)
        if taxonomy:
            label_ko, label_en = taxonomy
            return f"{label_ko} · {label_en}"
        # 방어적 폴백: 분류기가 만들어내지 않는 미지의 코드라도 code 자체는 노출하지 않는다.
        label_ko = category_labels.get(value, "")
        if label_ko:
            return label_ko
    return value


def _build_filter_field(
    field: str,
    select_id: str,
    label: str,
    facet_counts: dict[str, int],
    category_labels: dict[str, str],
    total_records: int,
) -> str:
    options = [f'<option value="">전체 ({total_records})</option>']
    for value in sorted(facet_counts.keys()):
        count = facet_counts[value]
        display = _option_label(field, value, category_labels)
        options.append(
            '<option value="{value}">{display} ({count})</option>'.format(
                value=html.escape(value, quote=True),
                display=html.escape(display),
                count=count,
            )
        )
    options_html = "\n        ".join(options)
    return (
        '    <label class="filter-field">\n'
        f'      <span class="filter-label">{html.escape(label)}</span>\n'
        f'      <select id="{select_id}">\n'
        f"        {options_html}\n"
        "      </select>\n"
        "    </label>"
    )


def _build_filters_html(
    facets: dict[str, Any],
    category_labels: dict[str, str],
    total_records: int,
) -> str:
    fields_html = []
    for field, select_id, label in _FILTER_SPECS:
        facet_counts = facets.get(field) or {}
        fields_html.append(
            _build_filter_field(field, select_id, label, facet_counts, category_labels, total_records)
        )
    return "\n".join(fields_html)


def build_search_page(export: dict[str, Any]) -> str:
    """Render a grm-findings-search/v1 envelope into a single offline HTML document.

    Raises ValueError if the export is not the expected schema version or is
    not flagged ready_for_viewer (i.e. it has blocking validation errors).
    """
    if not isinstance(export, dict) or export.get("schema_version") != _REQUIRED_EXPORT_SCHEMA_VERSION:
        raise ValueError(
            f"findings_search_page: export.schema_version must be {_REQUIRED_EXPORT_SCHEMA_VERSION!r}"
        )

    report = export.get("report")
    if not isinstance(report, dict) or not report.get("ready_for_viewer"):
        raise ValueError("findings_search_page: export.report.ready_for_viewer must be truthy")

    records = export.get("records") or []
    if not isinstance(records, list):
        raise ValueError("findings_search_page: export.records must be a list")

    facets = export.get("facets") or {}
    source_db = export.get("source_db") or {}
    taxonomy_version = str(export.get("taxonomy_version") or "")

    category_labels = _category_label_map(records)
    filters_html = _build_filters_html(facets, category_labels, len(records))

    data_json = json.dumps(records, ensure_ascii=False, sort_keys=True)
    data_json = data_json.replace("</", "<\\/")

    file_name_html = html.escape(str(source_db.get("file_name") or ""))
    record_count = len(records)
    taxonomy_version_html = html.escape(taxonomy_version)

    page = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GRM Findings 검색 v0</title>
<style>
{_APP_CSS}
</style>
</head>
<body>
<header class="app-header">
  <h1>GRM Findings 검색 v0</h1>
  <div class="app-meta">파일: {file_name_html} · 레코드 {record_count}건 · taxonomy {taxonomy_version_html}</div>
  <div class="app-disclosure">이 페이지는 로컬 전용 오프라인 도구이며, findings 데이터는 AI가 규제 문서에서 자동 추출한 내용입니다. 활용 전 반드시 원문을 대조 확인하십시오.</div>
</header>
<main class="app-main">
  <div class="controls">
    <div class="search-row">
      <input id="search-input" type="search" placeholder="검색어 (finding_text · firm_name · document_id)" autocomplete="off">
    </div>
    <div class="filters">
{filters_html}
      <button id="reset-btn" type="button">초기화</button>
    </div>
  </div>
  <div id="result-count" class="result-count"></div>
  <div id="result-list" class="result-list"></div>
  <div id="empty-message" class="empty-message" hidden>조건에 맞는 finding이 없습니다.</div>
</main>
<script type="application/json" id="findings-data">{data_json}</script>
<script>
{_APP_JS}
</script>
</body>
</html>
"""
    return page


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="FIND-1 M2c findings search export to offline HTML viewer")
    parser.add_argument("--input", required=True, help="Path to a grm-findings-search/v1 JSON export")
    parser.add_argument("--output", required=True, help="Output HTML file path")
    args = parser.parse_args(argv)

    try:
        export = json.loads(Path(args.input).read_text(encoding="utf-8"))
        page = build_search_page(export)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"findings_search_page: {exc}", file=sys.stderr)
        return 2

    try:
        with open(args.output, "w", encoding="utf-8", newline="\n") as f:
            f.write(page)
    except OSError as exc:
        print(f"findings_search_page: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
