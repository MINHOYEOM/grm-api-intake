/* GRM 자가점검 체크리스트 (/findings/checklist/) — 정적·클라이언트사이드, 순수 fetch.
 *
 * 042(findings_cfr_ranking)로 조항 순위를 받고, 그중 상위 N개를 043(findings_checklist)에
 * 넘겨 조항별 실제 지적 문장을 한 번에 받아 **인쇄·복사 가능한 점검표**로 조립한다.
 * 왕복은 딱 2회다(조항마다 검색을 돌리면 N+1 이 된다).
 *
 * ── 층 분리(불가침) ──────────────────────────────────────────────────────────
 *   · 조항 순위·필터(부 필터·보일러플레이트 제외·문서 수 집계)의 정본은 **042 하나뿐**이다.
 *     이 파일은 그 판단을 다시 하지 않고 정렬 키만 고른다(누적/최근). 필터를 복제하면
 *     트렌드 페이지와 체크리스트가 서로 다른 순위를 말하게 된다.
 *   · 사례 문장은 043 에서 온다. 그 함수는 security invoker 라 findings 의 RLS(010)가
 *     공개 게이트를 강제한다 — /findings/ 검색 페이지와 정확히 같은 노출 범위다.
 *     그래서 **사례 수가 042 의 문서 수보다 적을 수 있고**, 그 사실을 문서 말미에 적는다.
 *
 * 렌더는 전부 textContent/createElement 로만 한다(innerHTML 대입은 컨테이너 비우기 "" 뿐
 * — findings.js/trends.js 와 동일 XSS 계약). 업체명·지적 문장은 전부 textContent 로만 넣는다.
 *
 * [동기화 규칙] CFR_SECTION_LABELS 는 trends.js 의 동명 상수와 완전히 일치해야 한다
 * (web/tests/test_render.py 의 WebCfrSectionLabelsSyncTest 가 web/assets/*.js 를 글롭으로
 * 훑어 선언 파일을 전부 자동 발견해 대조한다 — 수동 목록이 낡아 침묵 통과하는 실패를
 * 이 저장소가 두 번 겪었기 때문에 전수 자동 열거로 고정한다).
 */
(function () {
  "use strict";

  var cfg = document.getElementById("grm-findings-cfg");
  var loadingEl = document.getElementById("cl-loading");
  var errorEl = document.getElementById("cl-error");
  var docEl = document.getElementById("cl-doc");
  var itemsEl = document.getElementById("cl-items");
  var docMetaEl = document.getElementById("cl-doc-meta");
  var docFootEl = document.getElementById("cl-doc-foot");
  var countEl = document.getElementById("cl-count");
  var sortEl = document.getElementById("cl-sort");
  var examplesEl = document.getElementById("cl-examples");
  var buildBtn = document.getElementById("cl-build");
  var exportEl = document.getElementById("cl-export");
  var printBtn = document.getElementById("cl-print");
  var copyBtn = document.getElementById("cl-copy");
  var csvBtn = document.getElementById("cl-csv");
  var copyMsgEl = document.getElementById("cl-copy-msg");
  if (!cfg || !loadingEl || !errorEl || !docEl || !itemsEl) return;

  var url = (cfg.getAttribute("data-url") || "").trim();
  var key = (cfg.getAttribute("data-key") || "").trim();

  // trends.js 의 동명 상수 verbatim 복제(동기화 테스트로 드리프트 차단, 파일 상단 계약 참조).
  var CFR_SECTION_LABELS = {
    "210.3": "정의",
    "211.22": "품질관리부서의 책임·권한",
    "211.25": "작업자 자격·교육",
    "211.28": "작업자 위생·복장",
    "211.42": "건물의 설계·구조(무균 구역 포함)",
    "211.46": "환기·공기 여과",
    "211.56": "청소·위생 관리",
    "211.58": "건물 유지관리",
    "211.63": "설비의 설계·규격·설치 위치",
    "211.67": "설비 세척·유지관리",
    "211.68": "전산화 설비 관리(접근권한·백업)",
    "211.80": "원자재·용기 일반 관리",
    "211.84": "원자재·용기 시험 및 합부 판정",
    "211.87": "승인된 원자재 재시험",
    "211.94": "용기·마개 적합성",
    "211.100": "생산·공정관리 절차서와 일탈 처리",
    "211.101": "원료 칭량·투입",
    "211.110": "공정 중 시료채취·시험",
    "211.111": "공정 단계별 시간 제한",
    "211.113": "미생물 오염 관리(무균공정 밸리데이션)",
    "211.115": "재작업",
    "211.125": "표시자재 불출 관리",
    "211.130": "포장·표시 작업 관리",
    "211.137": "사용기한 설정",
    "211.142": "보관 절차",
    "211.150": "출하·유통 절차",
    "211.160": "시험실 관리 일반(규격·시험방법의 타당성)",
    "211.165": "완제품 시험 및 출하 판정",
    "211.166": "안정성 시험",
    "211.167": "특수 시험(무균·발열성 등)",
    "211.170": "보관용 검체",
    "211.176": "페니실린 교차오염",
    "211.180": "기록·보고 일반(보관기간·연간 품질평가)",
    "211.182": "설비 사용·세척 기록",
    "211.186": "마스터 제조지시서",
    "211.188": "배치 제조기록서",
    "211.192": "제조기록 검토와 일탈 조사",
    "211.194": "시험기록",
    "211.198": "불만 처리 기록",
    "211.204": "반품 의약품",
    "211.208": "회수품 재생",
  };

  // 매핑에 없는 조항은 번호만 쓴다(추측 번역 금지 — trends.js countryLabelKo 와 동일 폴백).
  function sectionLabel(section) {
    return CFR_SECTION_LABELS[section] || "";
  }

  // 인쇄물 한 줄에 들어가야 읽히는 길이. 원문 전체는 [사례 원문 보기] 링크가 아니라
  // 검색 페이지(/findings/)에서 확인한다 — 체크리스트는 요지 확인용이다.
  var EXAMPLE_MAX_CHARS = 240;

  var state = { rows: [], meta: null };

  function el(tag, className, text) {
    var e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined && text !== null && text !== "") e.textContent = text;
    return e;
  }

  function fmtNum(n) {
    return String(Math.round(Number(n) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  // DB firm_name 에 이미 이스케이프돼 저장된 엔티티를 표시 직전에만 되돌린다
  // (findings.js/trends.js 의 동명 헬퍼와 동일 계약 — 순수 문자열 치환, XSS 무관).
  function decodeFirmDisplay(s) {
    return String(s || "").replace(/&amp;/g, "&").replace(/&#039;/g, "'");
  }

  function exampleText(f) {
    // 국문이 있으면 국문, 없으면 영어 원문(빈칸으로 두지 않는다 — 부재 어휘 규칙).
    var body = String(f.finding_text_ko || "").trim() || String(f.finding_text || "").trim();
    body = body.replace(/\s+/g, " ").trim();
    return body.length > EXAMPLE_MAX_CHARS ? body.slice(0, EXAMPLE_MAX_CHARS) + "…" : body;
  }

  function exampleMeta(f) {
    return [decodeFirmDisplay(f.firm_name), f.published_date, f.source]
      .filter(Boolean).join(" · ");
  }

  function rpcEndpoint(name) {
    return url.replace(/\/$/, "") + "/rest/v1/rpc/" + name;
  }

  function rpc(name, body) {
    return fetch(rpcEndpoint(name), {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      if (!r.ok) throw new Error(name + " " + r.status);
      return r.json();
    });
  }

  // ── 렌더 ────────────────────────────────────────────────────────────────
  function buildVerdictBox(idx) {
    var box = el("div", "cl-verdict");
    ["적합", "부적합", "해당없음"].forEach(function (label, i) {
      var lab = document.createElement("label");
      var input = document.createElement("input");
      input.type = "radio";
      // 같은 조항 안에서만 배타 선택되도록 항목별 name 을 준다.
      input.name = "cl-v-" + idx;
      input.value = label;
      lab.appendChild(input);
      lab.appendChild(document.createTextNode(label));
      box.appendChild(lab);
    });
    return box;
  }

  function buildItem(row, idx) {
    var item = el("div", "cl-item");
    var head = el("div", "cl-item-head");
    head.appendChild(el("span", "cl-item-no", String(idx + 1)));
    var id = el("div", "cl-item-id");
    id.appendChild(el("span", "cl-item-sec", "21 CFR " + row.section));
    var name = sectionLabel(row.section);
    if (name) id.appendChild(el("span", "cl-item-name", name));
    id.appendChild(el("span", "cl-item-cnt",
      "인용 문서 " + fmtNum(row.docs) + "건 · 최근 12개월 " + fmtNum(row.recent_docs) + "건"));
    head.appendChild(id);
    head.appendChild(buildVerdictBox(idx));
    item.appendChild(head);

    var ex = el("div", "cl-ex");
    ex.appendChild(el("h3", "cl-ex-h", "실제 지적 사례"));
    if (!row.examples.length) {
      // 042 는 전량 집계(definer), 043 은 공개 게이트(invoker) — 차이가 여기서 드러난다.
      ex.appendChild(el("p", "cl-ex-text", "국문으로 열람할 수 있는 사례가 아직 없습니다."));
    } else {
      row.examples.forEach(function (f) {
        var one = el("div", "cl-ex-item");
        var meta = el("p", "cl-ex-meta", exampleMeta(f));
        if (f.anchored === false) {
          // 같은 위반 블록이 여러 조항을 함께 인용한 경우 — 문장에 이 조항 번호가 없다.
          meta.appendChild(el("span", "cl-ex-loose", "(같은 지적에 여러 조항이 함께 인용됨)"));
        }
        one.appendChild(meta);
        one.appendChild(el("p", "cl-ex-text", exampleText(f)));
        ex.appendChild(one);
      });
    }
    item.appendChild(ex);

    var note = el("div", "cl-note-line");
    note.appendChild(el("span", "", "확인 결과 · 근거 문서"));
    note.appendChild(el("i", ""));
    item.appendChild(note);
    return item;
  }

  function renderDoc() {
    itemsEl.innerHTML = "";
    state.rows.forEach(function (row, i) { itemsEl.appendChild(buildItem(row, i)); });

    if (docMetaEl && state.meta) {
      docMetaEl.textContent =
        (state.focus ? "21 CFR " + state.focus + " 한 조항만 보는 중 · " : "") +
        "기준 " + state.meta.sortLabel + " · 조항 " + fmtNum(state.rows.length) +
        "개 · 조항당 사례 " + fmtNum(state.meta.examples) + "건 · 자료 기준일 " +
        state.meta.asOf + " · 출처 " + state.meta.sources;
    }
    if (docFootEl) {
      docFootEl.textContent =
        "이 표는 " + state.meta.partFilter + " 조항 중 규제 문서에 인용된 횟수가 많은 순으로 뽑은 것입니다. " +
        "FDA 483은 조항 대신 요구사항을 문장으로 적어 조항 인용이 거의 없어, 순위는 사실상 Warning Letter 기준입니다. " +
        "모든 경고서한 맺음말에 붙는 권고·정의 조항(" + state.meta.excluded + ")은 위반 인용이 아니라 제외했습니다. " +
        "사례는 국문 번역이 끝난 지적만 나오므로 인용 문서 수보다 적을 수 있습니다. " +
        "날짜는 실사한 날이 아니라 자료가 공개된 날입니다. 출처: GRM (grm-solutions.com)";
    }
    docEl.hidden = false;
    if (exportEl) exportEl.hidden = false;
  }

  // ── 내보내기 ────────────────────────────────────────────────────────────
  // 열 구성은 TSV(클립보드)·CSV(파일)가 완전히 같다 — 같은 표를 두 경로로 낼 뿐이다.
  function exportRows() {
    var maxEx = state.meta ? state.meta.examples : 0;
    var header = ["번호", "조항", "요지", "인용 문서수", "최근 12개월"];
    for (var i = 0; i < maxEx; i++) header.push("대표 사례 " + (i + 1));
    header.push("판정", "확인 결과·근거 문서");
    var out = [header];
    state.rows.forEach(function (row, idx) {
      var line = [
        String(idx + 1),
        "21 CFR " + row.section,
        sectionLabel(row.section),
        String(row.docs),
        String(row.recent_docs),
      ];
      for (var j = 0; j < maxEx; j++) {
        var f = row.examples[j];
        line.push(f ? "[" + exampleMeta(f) + "] " + exampleText(f) : "");
      }
      line.push("", "");     // 판정·근거는 사람이 채우는 빈 칸
      out.push(line);
    });
    return out;
  }

  // 탭·개행은 셀 경계라 공백으로 바꾼다(엑셀 붙여넣기가 깨지지 않게).
  function tsvCell(v) {
    return String(v == null ? "" : v).replace(/[\t\r\n]+/g, " ");
  }

  function toTsv(rows) {
    return rows.map(function (r) { return r.map(tsvCell).join("\t"); }).join("\n");
  }

  function csvCell(v) {
    var s = String(v == null ? "" : v).replace(/\r?\n/g, " ");
    return '"' + s.replace(/"/g, '""') + '"';
  }

  function toCsv(rows) {
    return rows.map(function (r) { return r.map(csvCell).join(","); }).join("\r\n");
  }

  function flashCopyMsg(msg) {
    if (!copyMsgEl) return;
    copyMsgEl.textContent = msg;
  }

  function copyTable() {
    var text = toTsv(exportRows());
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        flashCopyMsg("복사했습니다 — 엑셀에 붙여넣으세요.");
      }).catch(function () {
        flashCopyMsg("복사하지 못했습니다. CSV 내려받기를 이용해 주세요.");
      });
      return;
    }
    flashCopyMsg("이 브라우저에서는 복사가 지원되지 않습니다. CSV 내려받기를 이용해 주세요.");
  }

  function downloadCsv() {
    // BOM 을 붙이지 않으면 엑셀이 한글을 깨뜨린다(UTF-8 자동 인식 실패).
    var blob = new Blob(["﻿" + toCsv(exportRows())],
      { type: "text/csv;charset=utf-8;" });
    var href = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = href;
    a.download = "GRM_자가점검_체크리스트_" + (state.meta ? state.meta.asOf : "") + ".csv";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(href);
    flashCopyMsg("CSV 파일을 내려받았습니다.");
  }

  // ── 조립 ────────────────────────────────────────────────────────────────
  function selectedInt(node, fallback) {
    var v = node ? parseInt(node.value, 10) : NaN;
    return isNaN(v) ? fallback : v;
  }

  // [2026-09-03 조항 집중] `?section=211.192` 로 들어오면 그 조항 하나만 보여준다.
  // 용어사전의 "관련 조항"이 종전에는 eCFR(영문 법령)로만 나갔다 — 국문 사용자가
  // 조항을 눌렀을 때 우리 쪽 실제 지적사례로 올 수 있는 착지점이 없었다.
  // 형식 게이트(21 CFR 조항 번호 모양)를 통과한 값만 쓴다 — 임의 문자열이 RPC 인자로
  // 흘러가지 않게. 매치가 없으면 조용히 전체 목록으로 되돌린다(빈 화면보다 낫다).
  var FOCUS_SECTION = (function () {
    try {
      var v = new URLSearchParams(window.location.search).get("section") || "";
      return /^\d{3}\.\d+[a-z]?$/.test(v) ? v : "";
    } catch (e) { return ""; }
  })();

  function build() {
    var count = selectedInt(countEl, 15);
    var examples = selectedInt(examplesEl, 2);
    var sortKey = sortEl && sortEl.value === "recent" ? "recent_docs" : "docs";

    docEl.hidden = true;
    if (exportEl) exportEl.hidden = true;
    errorEl.hidden = true;
    loadingEl.hidden = false;
    loadingEl.textContent = "체크리스트를 만드는 중…";
    flashCopyMsg("");

    rpc("findings_cfr_ranking", { p_months: 12 }).then(function (rank) {
      var scope = (rank && rank.scope) || {};
      var items = ((rank && rank.items) || []).filter(function (i) {
        return (i[sortKey] || 0) > 0;
      }).sort(function (a, b) {
        return (b[sortKey] || 0) - (a[sortKey] || 0) || a.section.localeCompare(b.section);
      });
      if (!items.length) throw new Error("empty ranking");

      // 조항 집중은 **자르기 전에** 거른다 — 순위 20위 조항을 상위 15개에서 찾으면
      // 영영 안 나온다. 매치 0건이면 전체 목록을 그대로 쓴다.
      var focused = FOCUS_SECTION
        ? items.filter(function (i) { return i.section === FOCUS_SECTION; })
        : [];
      state.focus = focused.length ? FOCUS_SECTION : "";
      items = focused.length ? focused : items.slice(0, count);

      var sections = items.map(function (i) { return i.section; });
      return rpc("findings_checklist", { p_sections: sections, p_examples: examples })
        .then(function (detail) {
          var bySection = {};
          ((detail && detail.sections) || []).forEach(function (s) {
            bySection[s.section] = s.examples || [];
          });
          state.rows = items.map(function (i) {
            return {
              section: i.section,
              docs: i.docs || 0,
              recent_docs: i.recent_docs || 0,
              examples: bySection[i.section] || [],
            };
          });
          state.meta = {
            examples: examples,
            // ★sortKey 는 응답 필드명("recent_docs"/"docs")이지 셀렉트 값("recent")이
            // 아니다 — 여기서 "recent" 와 비교하면 항상 거짓이 되어, 최근순으로 정렬한
            // 표에도 인쇄물 머리에 "전체 누적 인용순"이 찍힌다(실제로 그랬다).
            sortLabel: sortKey === "recent_docs" ? "최근 12개월 인용순" : "전체 누적 인용순",
            asOf: scope.as_of || "",
            partFilter: scope.part_filter || "21 CFR 210/211",
            excluded: (scope.excluded_sections || []).join(" · "),
            sources: (scope.sources || []).map(function (s) {
              return s.source + " " + fmtNum(s.docs) + "건";
            }).join(" · "),
          };
          loadingEl.hidden = true;
          renderDoc();
        });
    }).catch(function () {
      loadingEl.hidden = true;
      docEl.hidden = true;
      if (exportEl) exportEl.hidden = true;
      errorEl.hidden = false;
    });
  }

  if (!url || !key) {
    loadingEl.textContent = "체크리스트 서비스 준비 중입니다.";
    return;
  }

  if (buildBtn) buildBtn.addEventListener("click", build);
  if (printBtn) printBtn.addEventListener("click", function () { window.print(); });
  if (copyBtn) copyBtn.addEventListener("click", copyTable);
  if (csvBtn) csvBtn.addEventListener("click", downloadCsv);
  build();
})();
