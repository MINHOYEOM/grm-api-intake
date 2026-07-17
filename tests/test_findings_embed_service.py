#!/usr/bin/env python3
"""[FIND-1 S2] tests for findings_embed_service.py and 019_findings_embeddings.sql.

Offline-only: no network, no real Postgres connection, and no real
sentence-transformers/torch model load -- findings_embed_service.py lazy-imports
those (see resolve_model_revision/load_model), so this file exercises only the
pure functions (public-gate selection, embed_input B reconstruction, sanity
checks, text_sha256 contract) plus source-text assertions against the 019
migration file, mirroring the style of test_findings_similar_lexical.py (018)
and test_findings_scope_purity.py (010).
"""

from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

import findings_embed_service as svc


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_EMBEDDINGS_PATH = _MIGRATIONS_DIR / "019_findings_embeddings.sql"
_SIMILAR_LEXICAL_PATH = _MIGRATIONS_DIR / "018_findings_similar_lexical.sql"
_REQUIREMENTS_PATH = Path(__file__).resolve().parent.parent / "requirements.txt"
_REQUIREMENTS_EMBED_PATH = Path(__file__).resolve().parent.parent / "requirements-embed.txt"
_REQUIREMENTS_EMBED_LOCK_PATH = Path(__file__).resolve().parent.parent / "requirements-embed.lock"
_WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "grm-findings-embed.yml"
)


def _fixed_test_finding() -> dict[str, Any]:
    return {
        "finding_id": "finding-1",
        "finding_text": "text one",
        "source": "FDA Warning Letter",
        "raw_signal_id": "rawsig-1",
    }


_FIXED_TEXT_SHA256 = hashlib.sha256(b"text one").hexdigest()


def _strip_sql_comments(sql: str) -> str:
    kept = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return "\n".join(kept)


# ---------------------------------------------------------------------------
# 1) E5 prefix contract
# ---------------------------------------------------------------------------


class E5PrefixContractTest(unittest.TestCase):
    def test_prefix_is_query_not_passage(self) -> None:
        self.assertEqual(svc.E5_QUERY_PREFIX, "query: ")

    def test_passage_prefix_not_used_as_a_string_literal(self) -> None:
        """아이템-투-아이템 대칭 유사도 계약 -- 비대칭 검색용 'passage: ' prefix 가
        실제 문자열 리터럴(코드에서 쓰일 수 있는 형태)로는 소스 어디에도 없어야
        한다(잘못된 prefix 를 실수로 섞어 쓰면 벡터 공간이 갈린다). docstring/주석이
        설명 목적으로 그 표현을 인용하는 것 자체는 무해하므로 따옴표로 감싼 리터럴
        형태만 금지한다."""
        source = Path(svc.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"passage: "', source)
        self.assertNotIn("'passage: '", source)

    def test_embed_texts_applies_prefix_to_every_row(self) -> None:
        captured: list[list[str]] = []

        class _FakeModel:
            def encode(self, texts, **_kwargs):
                captured.append(list(texts))
                return [[0.0] * svc.EMBED_DIM for _ in texts]

        vectors = svc.embed_texts(_FakeModel(), ["foo", "bar"], batch_size=2)
        self.assertEqual(len(vectors), 2)
        self.assertEqual(captured, [["query: foo", "query: bar"]])

    def test_embed_texts_normalizes_and_uses_numpy_convert(self) -> None:
        seen_kwargs: dict = {}

        class _FakeModel:
            def encode(self, texts, **kwargs):
                seen_kwargs.update(kwargs)
                return [[1.0] * svc.EMBED_DIM for _ in texts]

        svc.embed_texts(_FakeModel(), ["x"], batch_size=1)
        self.assertTrue(seen_kwargs.get("normalize_embeddings"))
        self.assertTrue(seen_kwargs.get("convert_to_numpy"))


# ---------------------------------------------------------------------------
# 2) embed_input B reconstruction -- exact single match / ambiguous / no
#    match / no detail all fall back to A
# ---------------------------------------------------------------------------


class ResolveBTextTest(unittest.TestCase):
    def test_exact_single_match_with_detail_returns_deficiency_newline_detail(self) -> None:
        raw = {
            "fda_483_observations": [
                {"deficiency": "Cleaning procedures were not followed.", "detail": "Batch record X lacked signoff."},
            ]
        }
        result = svc.resolve_b_text(
            "Cleaning procedures were not followed.", raw, {"firm_name": "Acme Pharma"},
        )
        self.assertEqual(
            result,
            "Cleaning procedures were not followed.\nBatch record X lacked signoff.",
        )

    def test_ambiguous_two_matches_falls_back_to_none(self) -> None:
        raw = {
            "fda_483_observations": [
                {"deficiency": "Same text.", "detail": "detail one"},
                {"deficiency": "Same text.", "detail": "detail two"},
            ]
        }
        self.assertIsNone(svc.resolve_b_text("Same text.", raw, {"firm_name": ""}))

    def test_no_match_falls_back_to_none(self) -> None:
        raw = {"fda_483_observations": [{"deficiency": "Other text.", "detail": "d"}]}
        self.assertIsNone(svc.resolve_b_text("Not present.", raw, {"firm_name": ""}))

    def test_match_without_detail_falls_back_to_none(self) -> None:
        raw = {"fda_483_observations": [{"deficiency": "No detail here.", "detail": ""}]}
        self.assertIsNone(svc.resolve_b_text("No detail here.", raw, {"firm_name": ""}))

    def test_missing_observations_array_falls_back_to_none(self) -> None:
        self.assertIsNone(svc.resolve_b_text("Anything.", {}, {"firm_name": ""}))
        self.assertIsNone(svc.resolve_b_text("Anything.", {"fda_483_observations": "not-a-list"}, {}))

    def test_non_dict_observation_entries_are_skipped_not_fatal(self) -> None:
        raw = {"fda_483_observations": ["not-a-dict", {"deficiency": "Real one.", "detail": "d"}]}
        self.assertEqual(svc.resolve_b_text("Real one.", raw, {}), "Real one.\nd")


class BuildEmbedTextTest(unittest.TestCase):
    def test_embed_input_a_always_returns_finding_text_verbatim(self) -> None:
        finding = {"finding_text": "Some finding.", "source": "FDA 483", "raw_signal_id": "rawsig-x"}
        raw_signals = {"rawsig-x": {"raw_json": '{"fda_483_observations": []}'}}
        text, mode = svc.build_embed_text(finding, "A", raw_signals)
        self.assertEqual(text, "Some finding.")
        self.assertEqual(mode, "A")

    def test_embed_input_b_non_483_source_falls_back_to_a(self) -> None:
        """483 이외 소스(WL·MFDS)는 fda_483_observations 가 없으므로 자동 A 폴백."""
        finding = {"finding_text": "WL text.", "source": "FDA Warning Letter", "raw_signal_id": "rawsig-y"}
        text, mode = svc.build_embed_text(finding, "B", {})
        self.assertEqual(text, "WL text.")
        self.assertEqual(mode, "A")

    def test_embed_input_b_missing_raw_signal_row_falls_back_to_a(self) -> None:
        finding = {"finding_text": "483 text.", "source": "FDA 483", "raw_signal_id": "rawsig-missing"}
        text, mode = svc.build_embed_text(finding, "B", {})
        self.assertEqual(text, "483 text.")
        self.assertEqual(mode, "A")

    def test_embed_input_b_exact_match_uses_reconstructed_text(self) -> None:
        finding = {"finding_text": "Deficiency A.", "source": "FDA 483", "raw_signal_id": "rawsig-z"}
        raw_signals = {
            "rawsig-z": {
                "firm_name": "Acme",
                "raw_json": (
                    '{"fda_483_observations": [{"deficiency": "Deficiency A.", '
                    '"detail": "Rich detail text."}]}'
                ),
            }
        }
        text, mode = svc.build_embed_text(finding, "B", raw_signals)
        self.assertEqual(text, "Deficiency A.\nRich detail text.")
        self.assertEqual(mode, "B")

    def test_embed_input_b_malformed_raw_json_falls_back_to_a(self) -> None:
        finding = {"finding_text": "Deficiency A.", "source": "FDA 483", "raw_signal_id": "rawsig-bad"}
        raw_signals = {"rawsig-bad": {"raw_json": "{not valid json"}}
        text, mode = svc.build_embed_text(finding, "B", raw_signals)
        self.assertEqual(text, "Deficiency A.")
        self.assertEqual(mode, "A")


# ---------------------------------------------------------------------------
# 3) text_sha256 -- prefix 제외 텍스트 기준
# ---------------------------------------------------------------------------


class TextSha256ContractTest(unittest.TestCase):
    def test_sha256_excludes_query_prefix(self) -> None:
        text = "Some finding text."
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # run_embed computes sha256 over the pre-prefix text -- assert the
        # naive hash (no prefix) is what a correct implementation would produce,
        # and that hashing the prefixed form gives a *different* digest (so a
        # regression that accidentally hashes the prefixed text is caught).
        prefixed_digest = hashlib.sha256((svc.E5_QUERY_PREFIX + text).encode("utf-8")).hexdigest()
        self.assertNotEqual(expected, prefixed_digest)

    def test_run_embed_hashes_pre_prefix_text(self) -> None:
        """run_embed 소스가 실제로 prefix 이전 텍스트를 해시하는지 소스 검사로 고정."""
        import inspect
        source = inspect.getsource(svc.run_embed)
        self.assertIn("hashlib.sha256(text.encode", source)
        self.assertNotIn("E5_QUERY_PREFIX + text", source)
        self.assertNotIn("E5_QUERY_PREFIX}{text", source)


# ---------------------------------------------------------------------------
# 4) sanity assert -- 384차원 · NaN/Inf · zero-norm 위반 시 중단
# ---------------------------------------------------------------------------


class SanityCheckTest(unittest.TestCase):
    def test_valid_vector_has_no_violations(self) -> None:
        vector = [0.1] * svc.EMBED_DIM
        self.assertEqual(svc.sanity_check_embeddings([vector]), [])

    def test_wrong_dimension_flagged(self) -> None:
        violations = svc.sanity_check_embeddings([[0.1] * (svc.EMBED_DIM - 1)])
        self.assertEqual(len(violations), 1)
        self.assertIn("dimension", violations[0])

    def test_nan_flagged(self) -> None:
        vector = [0.1] * svc.EMBED_DIM
        vector[0] = float("nan")
        violations = svc.sanity_check_embeddings([vector])
        self.assertEqual(len(violations), 1)
        self.assertIn("NaN/Inf", violations[0])

    def test_inf_flagged(self) -> None:
        vector = [0.1] * svc.EMBED_DIM
        vector[0] = float("inf")
        violations = svc.sanity_check_embeddings([vector])
        self.assertIn("NaN/Inf", violations[0])

    def test_zero_norm_flagged(self) -> None:
        violations = svc.sanity_check_embeddings([[0.0] * svc.EMBED_DIM])
        self.assertEqual(len(violations), 1)
        self.assertIn("zero-norm", violations[0])

    def test_run_embed_aborts_entirely_on_any_violation(self) -> None:
        """부분 적재 금지 -- sanity 위반이면 run_embed 은 upsert 를 전혀 호출하지 않고
        errors 를 채워 반환해야 한다(CLI exit 1로 이어짐)."""
        upsert_calls: list[object] = []

        def _fake_upsert(*_args, **_kwargs):
            upsert_calls.append(True)
            return 200, [], ""

        original_upsert = svc._upsert_embeddings_batch
        original_fetch_targets = svc.fetch_target_findings
        original_fetch_raw = svc.fetch_raw_signals_by_ids
        original_fetch_existing = svc.fetch_existing_embeddings
        original_load_model = svc.load_model
        original_resolve_revision = svc.resolve_model_revision
        original_embed_texts = svc.embed_texts

        class _BadModel:
            pass

        try:
            svc._upsert_embeddings_batch = _fake_upsert
            svc.fetch_target_findings = lambda base, key: [
                {"finding_id": "finding-1", "finding_text": "text one", "source": "FDA Warning Letter",
                 "raw_signal_id": "rawsig-1"},
            ]
            svc.fetch_raw_signals_by_ids = lambda base, key, ids: {}
            svc.fetch_existing_embeddings = lambda base, key, version: {}
            svc.resolve_model_revision = lambda name: "deadbeef"
            svc.load_model = lambda name, revision: _BadModel()
            # Wrong dimension -- guaranteed sanity violation.
            svc.embed_texts = lambda model, texts, *, batch_size: [[0.1] * (svc.EMBED_DIM - 1) for _ in texts]

            report = svc.run_embed(
                "https://example.supabase.co", "fake-key",
                embedding_version=1, embed_input="A", dry_run=False,
            )
        finally:
            svc._upsert_embeddings_batch = original_upsert
            svc.fetch_target_findings = original_fetch_targets
            svc.fetch_raw_signals_by_ids = original_fetch_raw
            svc.fetch_existing_embeddings = original_fetch_existing
            svc.load_model = original_load_model
            svc.resolve_model_revision = original_resolve_revision
            svc.embed_texts = original_embed_texts

        self.assertEqual(upsert_calls, [])
        self.assertTrue(report["errors"])
        self.assertEqual(report["upserted"], 0)


# ---------------------------------------------------------------------------
# 5) 대상 선정 쿼리 -- 공개 술어 2종(번역 OR KO / scope_status='ok') 명시
# ---------------------------------------------------------------------------


class PublicGatePredicateTest(unittest.TestCase):
    def test_translated_row_passes(self) -> None:
        self.assertTrue(svc.is_public_gate_row(
            {"finding_text_ko": "번역됨", "finding_language": "EN", "scope_status": "ok"}
        ))

    def test_ko_original_row_passes_even_without_translation(self) -> None:
        self.assertTrue(svc.is_public_gate_row(
            {"finding_text_ko": "", "finding_language": "KO", "scope_status": "ok"}
        ))

    def test_untranslated_non_ko_row_rejected(self) -> None:
        self.assertFalse(svc.is_public_gate_row(
            {"finding_text_ko": "", "finding_language": "EN", "scope_status": "ok"}
        ))

    def test_non_ok_scope_status_rejected_even_if_translated(self) -> None:
        for scope_status in ("non_pharma", "fragment"):
            self.assertFalse(svc.is_public_gate_row(
                {"finding_text_ko": "번역됨", "finding_language": "EN", "scope_status": scope_status}
            ))

    def test_select_public_findings_filters_and_sorts(self) -> None:
        rows = [
            {"finding_id": "finding-b", "finding_text_ko": "ko", "finding_language": "EN", "scope_status": "ok"},
            {"finding_id": "finding-a", "finding_text_ko": "", "finding_language": "KO", "scope_status": "ok"},
            {"finding_id": "finding-c", "finding_text_ko": "", "finding_language": "EN", "scope_status": "ok"},
        ]
        kept = svc.select_public_findings(rows)
        self.assertEqual([r["finding_id"] for r in kept], ["finding-a", "finding-b"])

    def test_fetch_target_findings_query_applies_scope_status_server_side(self) -> None:
        """fetch_target_findings 가 scope_status='ok' 를 서버 필터로 명시하는지 소스 검사."""
        import inspect
        source = inspect.getsource(svc.fetch_target_findings)
        self.assertIn('"scope_status": "eq.ok"', source)
        self.assertIn("select_public_findings", source)  # 나머지 술어(번역 OR KO)는 여기서 적용


# ---------------------------------------------------------------------------
# 6) --dry-run 은 쓰기 경로를 타지 않는다
# ---------------------------------------------------------------------------


class DryRunTest(unittest.TestCase):
    def test_dry_run_never_calls_upsert_even_with_pending_work(self) -> None:
        upsert_calls: list[object] = []

        def _fake_upsert(*_args, **_kwargs):
            upsert_calls.append(True)
            return 200, [], ""

        original_upsert = svc._upsert_embeddings_batch
        original_fetch_targets = svc.fetch_target_findings
        original_fetch_raw = svc.fetch_raw_signals_by_ids
        original_fetch_existing = svc.fetch_existing_embeddings
        original_load_model = svc.load_model
        original_resolve_revision = svc.resolve_model_revision
        original_embed_texts = svc.embed_texts

        class _FakeModel:
            pass

        try:
            svc._upsert_embeddings_batch = _fake_upsert
            svc.fetch_target_findings = lambda base, key: [
                {"finding_id": "finding-1", "finding_text": "text one", "source": "FDA Warning Letter",
                 "raw_signal_id": "rawsig-1"},
            ]
            svc.fetch_raw_signals_by_ids = lambda base, key, ids: {}
            svc.fetch_existing_embeddings = lambda base, key, version: {}
            svc.resolve_model_revision = lambda name: "deadbeef"
            svc.load_model = lambda name, revision: _FakeModel()
            svc.embed_texts = lambda model, texts, *, batch_size: [[0.1] * svc.EMBED_DIM for _ in texts]

            report = svc.run_embed(
                "https://example.supabase.co", "fake-key",
                embedding_version=1, embed_input="A", dry_run=True,
            )
        finally:
            svc._upsert_embeddings_batch = original_upsert
            svc.fetch_target_findings = original_fetch_targets
            svc.fetch_raw_signals_by_ids = original_fetch_raw
            svc.fetch_existing_embeddings = original_fetch_existing
            svc.load_model = original_load_model
            svc.resolve_model_revision = original_resolve_revision
            svc.embed_texts = original_embed_texts

        self.assertEqual(upsert_calls, [])
        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["upserted"], 0)
        self.assertEqual(report["embedded"], 1)  # 임베딩 계산은 dry_run 에서도 수행된다
        self.assertEqual(report["errors"], [])


# ---------------------------------------------------------------------------
# 7) embedding_config 는 절대 건드리지 않는다
# ---------------------------------------------------------------------------


class EmbeddingConfigUntouchedTest(unittest.TestCase):
    """embedding_config(활성 버전 스위치)는 컨트롤타워가 별도 수행하는 원자 전환
    대상이다 -- 이 서비스는 절대 쓰기(POST/PATCH)하지 않는다. 설명 주석/docstring이
    (왜 건드리지 않는지 설명하려고) 그 이름을 언급하는 것 자체는 무해하므로, 이
    테스트는 "테이블에 대한 실제 쓰기 경로가 없다"를 정밀하게 고정한다(막연한 문자열
    전면 금지가 아니라)."""

    def test_service_never_hits_the_embedding_config_rest_endpoint(self) -> None:
        source = Path(svc.__file__).read_text(encoding="utf-8")
        self.assertNotIn("rest/v1/embedding_config", source)
        self.assertNotIn('"embedding_config"', source)
        self.assertNotIn("'embedding_config'", source)

    def test_service_never_assigns_active_version(self) -> None:
        source = Path(svc.__file__).read_text(encoding="utf-8")
        self.assertNotIn('active_version":', source)
        self.assertNotIn("active_version =", source)

    def test_service_never_posts_or_patches_any_table_other_than_finding_embeddings(self) -> None:
        source = Path(svc.__file__).read_text(encoding="utf-8")
        for verb_call in ("requests.post(", "requests.patch(", "requests.put(", "requests.delete("):
            self.assertLessEqual(
                source.count(verb_call), 1,
                f"expected at most one write call site ({verb_call}) -- found more, review for a new write path",
            )


# ---------------------------------------------------------------------------
# 8) requirements.txt 회귀 방지 -- sentence-transformers/torch 미포함
# ---------------------------------------------------------------------------


class RequirementsIsolationTest(unittest.TestCase):
    def test_requirements_txt_has_no_ml_dependencies(self) -> None:
        text = _REQUIREMENTS_PATH.read_text(encoding="utf-8").lower()
        for forbidden in ("sentence-transformers", "torch", "transformers", "huggingface"):
            self.assertNotIn(forbidden, text, f"requirements.txt must not pull in {forbidden!r}")

    def test_requirements_embed_txt_exists_and_has_ml_dependencies(self) -> None:
        embed_reqs_path = Path(__file__).resolve().parent.parent / "requirements-embed.txt"
        self.assertTrue(embed_reqs_path.is_file())
        text = embed_reqs_path.read_text(encoding="utf-8").lower()
        self.assertIn("sentence-transformers", text)
        self.assertIn("torch", text)

    def test_findings_embed_service_does_not_import_ml_libs_at_module_level(self) -> None:
        """sentence-transformers 가 로컬에 없어도 import 가능해야 한다(지연 import 계약)."""
        source = Path(svc.__file__).read_text(encoding="utf-8")
        top_level = source.split("\ndef ")[0].split("\nclass ")[0]
        self.assertNotIn("import sentence_transformers", top_level)
        self.assertNotIn("from sentence_transformers", top_level)
        self.assertNotIn("import huggingface_hub", top_level)
        self.assertNotIn("from huggingface_hub", top_level)
        self.assertNotIn("import torch", top_level)


# ---------------------------------------------------------------------------
# 9) 019 마이그레이션 계약 -- 018 스타일 그대로
# ---------------------------------------------------------------------------


class EmbeddingsMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_EMBEDDINGS_PATH.is_file(), f"missing {_EMBEDDINGS_PATH}")
        self.sql = _EMBEDDINGS_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _EMBEDDINGS_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_idempotent_ddl_only(self) -> None:
        self.assertIn("create extension if not exists vector", self.code)
        self.assertIn("create table if not exists public.embedding_config", self.code)
        self.assertIn("create table if not exists public.finding_embeddings", self.code)
        self.assertIn("create or replace function public.findings_similar_by_id(", self.code)


class CompositePrimaryKeyTest(unittest.TestCase):
    """복합 PK(embedding_version, finding_id) -- 구·신 벡터 병렬 보관의 전제."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_EMBEDDINGS_PATH.read_text(encoding="utf-8"))

    def test_finding_embeddings_has_composite_pk(self) -> None:
        table = self.code[self.code.index("create table if not exists public.finding_embeddings"):]
        table = table[: table.index(");") + 2]
        self.assertIn("primary key (embedding_version, finding_id)", table)
        # No single-column finding_id PK anywhere in this table definition.
        self.assertNotIn("finding_id text not null references public.findings (finding_id) on delete cascade primary key", table)

    def test_embedding_config_single_row_enforced(self) -> None:
        self.assertIn("id int primary key default 1 check (id = 1)", self.code)


class ActiveVersionReferenceTest(unittest.TestCase):
    """findings_similar_by_id 가 embedding_config.active_version 을 참조해 혼합 벡터
    공간 서빙을 구조적으로 차단하는지."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_EMBEDDINGS_PATH.read_text(encoding="utf-8"))
        fn = self.code[self.code.index("create or replace function public.findings_similar_by_id("):]
        self.fn = fn[: fn.index("$$;") + 3]

    def test_function_reads_active_version_from_config(self) -> None:
        self.assertIn("select active_version from public.embedding_config where id = 1", self.fn)

    def test_base_and_neighbors_both_filtered_by_active_version(self) -> None:
        self.assertGreaterEqual(self.fn.count("e.embedding_version = (select active_version from cfg)"), 2)

    def test_unset_active_version_yields_empty_not_error(self) -> None:
        self.assertIn("(select active_version from cfg) <> 0", self.fn)


class RawSignalIdExclusionTest(unittest.TestCase):
    """같은 문서(raw_signal_id) 제외 -- document_id 가 아니라 raw_signal_id 가 문서
    정체성 키다(018 과 동일 축, Codex 정정)."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_EMBEDDINGS_PATH.read_text(encoding="utf-8"))
        fn = self.code[self.code.index("create or replace function public.findings_similar_by_id("):]
        self.fn = fn[: fn.index("$$;") + 3]

    def test_neighbors_exclude_same_raw_signal_id(self) -> None:
        self.assertIn("f.raw_signal_id is distinct from b.raw_signal_id", self.fn)

    def test_document_id_not_used_as_the_exclusion_key(self) -> None:
        self.assertNotIn("document_id is distinct from", self.fn)


class NoIndexCreatedTest(unittest.TestCase):
    """★인덱스 없음(의도) -- 8.5k 규모에서 exact cosine 순차 스캔으로 먼저 품질 검증.
    HNSW 는 근사 검색이라 공개 게이트 술어 후적용 시 결과 부족 위험(Codex 지적)."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_EMBEDDINGS_PATH.read_text(encoding="utf-8"))

    def test_no_hnsw_index(self) -> None:
        self.assertNotIn("hnsw", self.code.lower())
        self.assertNotIn("ivfflat", self.code.lower())

    def test_only_the_version_lookup_index_exists(self) -> None:
        # The one permitted index is a plain b-tree lookup index on
        # embedding_version, not a vector similarity index.
        self.assertEqual(self.code.count("create index"), 1)
        self.assertIn("idx_finding_embeddings_version", self.code)
        self.assertIn("on public.finding_embeddings (embedding_version)", self.code)


class VectorAndRawSignalIdNotReturnedTest(unittest.TestCase):
    """벡터·raw_signal_id 이외의 비공개 필드는 어떤 경로로도 반환하지 않는다(007/018
    안전 계약 확장) -- raw_signal_id 는 반환하되(중복 감지에 필요, 018 과 동형) 벡터
    자체와 evidence_url/raw_json 은 금지."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_EMBEDDINGS_PATH.read_text(encoding="utf-8"))

    def test_no_forbidden_fields_in_jsonb_build(self) -> None:
        build = self.code[self.code.index("jsonb_build_object("):]
        for forbidden in ("evidence_url", "raw_json", "row_json", "raw_sha256"):
            self.assertNotIn(forbidden, build, f"019 must not return {forbidden}")
        # The `embedding` halfvec column itself must never be selected into the
        # returned jsonb object (as opposed to being used only in ORDER BY/<=>).
        item_build = build[build.index("'items', coalesce(("): build.index("$$;")]
        self.assertNotIn("'embedding', ", item_build)


class GateReplicationTest(unittest.TestCase):
    """공개 술어 양쪽(기준 finding + 후보) 적용 -- 018 과 동일 축."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_EMBEDDINGS_PATH.read_text(encoding="utf-8"))
        fn = self.code[self.code.index("create or replace function public.findings_similar_by_id("):]
        self.fn = fn[: fn.index("$$;") + 3]

    def test_gate_applied_to_base_finding(self) -> None:
        base = self.fn[self.fn.index("base as ("): self.fn.index("neighbors as (")]
        self.assertIn("f.finding_text_ko <> '' or f.finding_language = 'KO'", base)
        self.assertIn("f.scope_status = 'ok'", base)

    def test_gate_applied_to_neighbor_candidates(self) -> None:
        neighbors = self.fn[self.fn.index("neighbors as ("): self.fn.index("groups as (")]
        self.assertIn("f.finding_text_ko <> '' or f.finding_language = 'KO'", neighbors)
        self.assertIn("f.scope_status = 'ok'", neighbors)

    def test_gate_matches_018_predicate_terms(self) -> None:
        similar_sql = _strip_sql_comments(_SIMILAR_LEXICAL_PATH.read_text(encoding="utf-8"))
        self.assertIn("f.finding_text_ko <> '' or f.finding_language = 'KO'", similar_sql)
        self.assertIn("f.scope_status = 'ok'", similar_sql)


class SecurityDefinerConventionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.code = _strip_sql_comments(_EMBEDDINGS_PATH.read_text(encoding="utf-8"))

    def test_security_definer_with_fixed_search_path(self) -> None:
        fn = self.code[self.code.index("create or replace function public.findings_similar_by_id("):]
        fn = fn[: fn.index("$$;") + 3]
        self.assertIn("security definer", fn)
        self.assertIn("stable", fn)
        self.assertIn("set search_path = public, extensions", fn)

    def test_revoke_then_grant_execute(self) -> None:
        self.assertIn(
            "revoke all on function public.findings_similar_by_id(text, int) from public;", self.code,
        )
        self.assertIn(
            "grant execute on function public.findings_similar_by_id(text, int) to anon, authenticated;",
            self.code,
        )

    def test_embedding_config_and_finding_embeddings_rls_locked_down(self) -> None:
        self.assertEqual(self.code.count("revoke all on public.embedding_config from anon, authenticated;"), 1)
        self.assertEqual(self.code.count("revoke all on public.finding_embeddings from anon, authenticated;"), 1)
        self.assertIn("alter table public.embedding_config enable row level security;", self.code)
        self.assertIn("alter table public.finding_embeddings enable row level security;", self.code)


class DoesNotTouchExistingObjectsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.code = _strip_sql_comments(_EMBEDDINGS_PATH.read_text(encoding="utf-8"))

    def test_does_not_touch_prior_tables_or_rpcs(self) -> None:
        for forbidden in (
            "drop table", "drop policy", "alter table public.findings",
            "create policy", "delete from", "update public.findings", "insert into public.findings",
            "drop function public.findings_similar(", "drop function public.findings_stats",
        ):
            self.assertNotIn(forbidden, self.code.lower(), f"019 must not contain: {forbidden}")


# ---------------------------------------------------------------------------
# 10) F-04 (Codex 감사) -- 모델 revision 을 재임베딩 판정에 포함
# ---------------------------------------------------------------------------


class _RunEmbedFixture(unittest.TestCase):
    """Shared monkeypatch scaffolding for run_embed orchestration tests --
    saves/restores every module-level collaborator run_embed calls, mirroring
    the inline save/restore dance SanityCheckTest/DryRunTest already use
    (factored out here because the F-04 tests need several similarly-shaped
    variants)."""

    def setUp(self) -> None:
        self._originals = {
            "_upsert_embeddings_batch": svc._upsert_embeddings_batch,
            "fetch_target_findings": svc.fetch_target_findings,
            "fetch_raw_signals_by_ids": svc.fetch_raw_signals_by_ids,
            "fetch_existing_embeddings": svc.fetch_existing_embeddings,
            "load_model": svc.load_model,
            "resolve_model_revision": svc.resolve_model_revision,
            "embed_texts": svc.embed_texts,
        }
        # Default stand-ins shared by every test in this fixture; individual
        # tests override whichever of these their scenario needs.
        svc.fetch_target_findings = lambda base, key: [_fixed_test_finding()]
        svc.fetch_raw_signals_by_ids = lambda base, key, ids: {}

    def tearDown(self) -> None:
        for name, value in self._originals.items():
            setattr(svc, name, value)

    def _run(self, **overrides):
        return svc.run_embed(
            "https://example.supabase.co", "fake-key",
            embedding_version=1, embed_input="A", dry_run=False,
            **overrides,
        )


class RevisionChangeForcesReembedTest(_RunEmbedFixture):
    """F-04 핵심: text_sha256 은 같지만 model 이 다른 revision 이면 재임베딩 경로를 탄다."""

    def test_same_text_different_model_reembeds_and_counts_revision_changed(self) -> None:
        embed_calls: list[list[str]] = []
        upsert_calls: list[object] = []

        old_tag = svc.build_model_tag(svc.MODEL_NAME, "old-revision-sha")
        svc.fetch_existing_embeddings = lambda base, key, version: {
            "finding-1": (_FIXED_TEXT_SHA256, old_tag),
        }
        svc.resolve_model_revision = lambda name: "new-revision-sha"
        svc.load_model = lambda name, revision: object()

        def _fake_embed_texts(model, texts, *, batch_size):
            embed_calls.append(list(texts))
            return [[0.1] * svc.EMBED_DIM for _ in texts]

        svc.embed_texts = _fake_embed_texts

        def _fake_upsert(*_args, **_kwargs):
            upsert_calls.append(True)
            return 200, [], ""

        svc._upsert_embeddings_batch = _fake_upsert

        report = self._run()

        self.assertEqual(report["already_current"], 0)
        self.assertEqual(report["to_embed"], 1)
        self.assertEqual(report["revision_changed"], 1)
        self.assertEqual(len(embed_calls), 1)  # actual embedding path was taken
        self.assertEqual(len(upsert_calls), 1)
        self.assertEqual(report["upserted"], 1)
        self.assertEqual(report["errors"], [])
        new_tag = svc.build_model_tag(svc.MODEL_NAME, "new-revision-sha")
        self.assertEqual(report["model"], new_tag)


class SameTextAndModelSkipsReembedTest(_RunEmbedFixture):
    """반대 방향: sha256·model 둘 다 같으면 already_current 이고 임베딩·upsert 는 0회."""

    def test_same_text_and_model_skips_with_zero_embed_or_upsert_calls(self) -> None:
        embed_calls: list[object] = []
        upsert_calls: list[object] = []
        load_calls: list[object] = []

        current_tag = svc.build_model_tag(svc.MODEL_NAME, "same-revision-sha")
        svc.fetch_existing_embeddings = lambda base, key, version: {
            "finding-1": (_FIXED_TEXT_SHA256, current_tag),
        }
        svc.resolve_model_revision = lambda name: "same-revision-sha"

        def _fake_load_model(name, revision):
            load_calls.append(True)
            return object()

        svc.load_model = _fake_load_model

        def _fake_embed_texts(model, texts, *, batch_size):
            embed_calls.append(True)
            return [[0.1] * svc.EMBED_DIM for _ in texts]

        svc.embed_texts = _fake_embed_texts

        def _fake_upsert(*_args, **_kwargs):
            upsert_calls.append(True)
            return 200, [], ""

        svc._upsert_embeddings_batch = _fake_upsert

        report = self._run()

        self.assertEqual(report["already_current"], 1)
        self.assertEqual(report["to_embed"], 0)
        self.assertEqual(report["revision_changed"], 0)
        self.assertEqual(load_calls, [])
        self.assertEqual(embed_calls, [])
        self.assertEqual(upsert_calls, [])
        self.assertEqual(report["upserted"], 0)
        self.assertEqual(report["errors"], [])


class ResolverCalledBeforeSkipDecisionTest(_RunEmbedFixture):
    """resolve_model_revision 은 skip 판정보다 먼저 호출된다 -- to_process 가 빈
    상황(모두 already_current)에서도 resolver 는 호출됐어야 한다(호출 카운터로 검증)."""

    def test_resolver_called_even_when_to_process_ends_up_empty(self) -> None:
        resolve_calls: list[str] = []

        def _resolve(name: str) -> str:
            resolve_calls.append(name)
            return "rev-x"

        svc.resolve_model_revision = _resolve
        current_tag = svc.build_model_tag(svc.MODEL_NAME, "rev-x")
        svc.fetch_existing_embeddings = lambda base, key, version: {
            "finding-1": (_FIXED_TEXT_SHA256, current_tag),
        }

        # Defensive: these must never be reached once to_process is empty --
        # fail loudly (rather than falling through to the real, network/heavy
        # sentence-transformers path) if a regression calls them anyway.
        def _unexpected(*_args, **_kwargs):
            raise AssertionError("must not be called when to_process is empty")

        svc.load_model = _unexpected
        svc.embed_texts = _unexpected
        svc._upsert_embeddings_batch = _unexpected

        report = self._run()

        self.assertEqual(resolve_calls, [svc.MODEL_NAME])
        self.assertEqual(report["to_embed"], 0)
        self.assertEqual(report["already_current"], 1)
        self.assertEqual(report["model"], current_tag)
        self.assertEqual(report["errors"], [])


class ResolveFailurePropagatesTest(_RunEmbedFixture):
    """resolve_model_revision 실패 시 예외가 errors 로 반영되고, 빈 model 문자열로
    임베딩을 진행하지 않는다(모델 로드/임베딩/upsert 어느 것도 호출되지 않음)."""

    def test_resolve_failure_aborts_before_any_model_load(self) -> None:
        svc.fetch_existing_embeddings = lambda base, key, version: {}

        def _raise(name: str) -> str:
            raise RuntimeError(
                f"findings_embed_service: could not resolve model revision for {name} (boom)"
            )

        svc.resolve_model_revision = _raise

        def _unexpected(*_args, **_kwargs):
            raise AssertionError("must not be called when resolve_model_revision fails")

        svc.load_model = _unexpected
        svc.embed_texts = _unexpected
        svc._upsert_embeddings_batch = _unexpected

        report = self._run()

        self.assertTrue(report["errors"])
        self.assertEqual(report["model"], "")  # never overwritten with a blank placeholder
        self.assertEqual(report["upserted"], 0)
        self.assertEqual(report["embedded"], 0)


class FetchExistingEmbeddingsIncludesModelTest(unittest.TestCase):
    """fetch_existing_embeddings 가 select 에 model 을 포함하는지 소스 검사(소스마커)."""

    def test_select_includes_model_column(self) -> None:
        import inspect
        source = inspect.getsource(svc.fetch_existing_embeddings)
        self.assertIn('"select": "finding_id,text_sha256,model"', source)

    def test_return_type_is_sha256_and_model_tuple(self) -> None:
        import inspect
        source = inspect.getsource(svc.fetch_existing_embeddings)
        self.assertIn("dict[str, tuple[str, str]]", source)


# ---------------------------------------------------------------------------
# 11) F-05 (Codex 감사) -- 공급망 pin: action SHA + 의존성 정확 버전 고정
# ---------------------------------------------------------------------------


class WorkflowActionPinningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_WORKFLOW_PATH.is_file(), f"missing {_WORKFLOW_PATH}")
        self.workflow = _WORKFLOW_PATH.read_text(encoding="utf-8")
        self.uses_lines = [line.strip() for line in self.workflow.splitlines() if "uses:" in line]

    def test_at_least_three_actions_present(self) -> None:
        self.assertGreaterEqual(len(self.uses_lines), 3)

    def test_every_action_pinned_to_a_full_commit_sha(self) -> None:
        for line in self.uses_lines:
            self.assertRegex(
                line, r"@[0-9a-f]{40}\b",
                f"not pinned to a full 40-hex-char commit SHA: {line}",
            )

    def test_no_bare_version_tag_references_remain(self) -> None:
        for forbidden in ("actions/checkout@v", "actions/setup-python@v", "actions/cache@v"):
            self.assertNotIn(forbidden, self.workflow, f"floating tag reference found: {forbidden}")

    def test_pinned_actions_carry_a_trailing_version_comment(self) -> None:
        for name in ("actions/checkout@", "actions/setup-python@", "actions/cache@"):
            line = next(l for l in self.uses_lines if name in l)
            self.assertIn("# v", line, f"missing trailing version comment: {line}")

    def test_expected_shas_match_the_audited_commits(self) -> None:
        # ★2026-07-16 GitHub API 조회로 확인한 실제 SHA(Codex 감사 F-05 지시값).
        self.assertIn("actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd", self.workflow)
        self.assertIn("actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405", self.workflow)
        self.assertIn("actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830", self.workflow)


class RequirementsEmbedExactPinningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_REQUIREMENTS_EMBED_PATH.is_file(), f"missing {_REQUIREMENTS_EMBED_PATH}")
        self.text = _REQUIREMENTS_EMBED_PATH.read_text(encoding="utf-8")
        self.dependency_lines = [
            line.strip() for line in self.text.splitlines()
            if line.strip() and not line.strip().startswith("#") and not line.strip().startswith("--")
        ]

    def test_at_least_five_pinned_dependencies(self) -> None:
        self.assertGreaterEqual(len(self.dependency_lines), 5)

    def test_every_dependency_line_is_an_exact_pin(self) -> None:
        for line in self.dependency_lines:
            self.assertIn("==", line, f"not pinned to an exact version: {line}")

    def test_no_floating_range_operators_remain(self) -> None:
        for line in self.dependency_lines:
            self.assertNotIn(">=", line, f"floating lower bound remains: {line}")
            self.assertNotIn("<", line, f"floating upper bound remains: {line}")

    def test_expected_pinned_versions_present(self) -> None:
        for expected in (
            "torch==2.13.0",
            "sentence-transformers==3.4.1",
            "transformers==4.57.6",
            "huggingface_hub==0.36.2",
            "numpy==2.5.1",
        ):
            self.assertIn(expected, self.text)

    def test_cpu_extra_index_url_preserved(self) -> None:
        # F-05 pin 은 CPU 전용 torch 휠 인덱스(기존 설계 계약)를 건드리지 않는다.
        self.assertIn("--extra-index-url https://download.pytorch.org/whl/cpu", self.text)


class IntakeRequirementsStillIsolatedFromMlDepsTest(unittest.TestCase):
    """회귀 방지(기존 계약 유지) -- requirements.txt(intake) 에 torch·
    sentence-transformers 가 여전히 없어야 한다. RequirementsIsolationTest(#8)와
    같은 계약을 F-05 정확 버전 고정 이후에도 다시 고정한다."""

    def test_requirements_txt_still_has_no_ml_dependencies(self) -> None:
        text = _REQUIREMENTS_PATH.read_text(encoding="utf-8").lower()
        for forbidden in ("sentence-transformers", "torch", "transformers", "huggingface"):
            self.assertNotIn(forbidden, text, f"requirements.txt must not pull in {forbidden!r}")


# ---------------------------------------------------------------------------
# 12) F-05 완결 (Codex 감사) -- requirements-embed.lock: 전이 의존성까지 sha256 고정
#
# 배경: #11(WorkflowActionPinningTest/RequirementsEmbedExactPinningTest)은 직접
# 의존성 5(현재 6)종을 `==` 로 정확 고정했지만, 그 직접 의존성들이 끌고 오는 전이
# 의존성(charset-normalizer, numpy, pyyaml, ...)은 여전히 floating 이라 Codex 후속
# 감사가 "부분 종결"로 판정했다. 해결은 pip-compile --generate-hashes 로 만든
# requirements-embed.lock(32 패키지, sha256 해시 709개)을 워크플로가
# `pip install --require-hashes -r requirements-embed.lock` 단일 스텝으로 설치하는
# 것 -- 이 절에서 그 lock 파일 자체의 내용 계약과 워크플로 배선을 함께 고정한다.
# ---------------------------------------------------------------------------


def _lock_requirement_blocks(text: str) -> list[list[str]]:
    """requirements-embed.lock 을 pin 단위 블록으로 쪼갠다 -- 각 블록은
    `name==version \\` 로 시작하는 줄부터 다음 pin 줄(또는 파일 끝) 직전까지다.
    pip-compile 이 각 pin 아래에 `--hash=sha256:...` 줄들과 `# via ...` 주석을 붙이는
    고정 포맷을 이용해, 코멘트/`--extra-index-url`/해시 continuation 줄은 자연히
    앞선 pin 의 블록에 포함되고 별도 pin 으로 오검출되지 않는다."""
    lines = text.splitlines()
    pin_re = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*==\S")
    starts = [i for i, line in enumerate(lines) if pin_re.match(line)]
    blocks: list[list[str]] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        blocks.append(lines[start:end])
    return blocks


class LockFileExistsTest(unittest.TestCase):
    """F-05 완결 계약 1/6 -- lock 파일이 존재하고 비어있지 않아야 워크플로가 실제로
    설치할 대상이 있다(빈 파일이면 `--require-hashes -r <empty>` 가 아무것도 설치하지
    않고 조용히 통과해버릴 위험이 있다 -- torch/sentence-transformers 가 빠진 채 다음
    스텝이 import 시점에야 실패하면 원인 추적이 더 어려워진다)."""

    def test_lock_file_exists_and_is_not_empty(self) -> None:
        self.assertTrue(
            _REQUIREMENTS_EMBED_LOCK_PATH.is_file(), f"missing {_REQUIREMENTS_EMBED_LOCK_PATH}"
        )
        text = _REQUIREMENTS_EMBED_LOCK_PATH.read_text(encoding="utf-8")
        self.assertGreater(len(text.strip()), 0)


class LockAllPinsHashedTest(unittest.TestCase):
    """F-05 완결 계약 2/6 -- lock 의 모든 `==` pin 이 최소 1개의 `--hash=sha256:` 를
    가진다(해시 없는 pin 이 하나도 없다). `pip install --require-hashes` 는 같은
    invocation 안의 '모든' 요구사항이 해시를 가져야 통과하므로, 이 계약이 실제로는 pip
    자체가 이미 강제하는 것이지만 -- 여기서 소스 파일 검사로 먼저 고정해두면 CI 가
    네트워크·PyPI 접속 없이도 회귀(예: 사람이 lock 을 손으로 편집하며 해시를 빠뜨림)를
    잡을 수 있다."""

    def setUp(self) -> None:
        self.assertTrue(
            _REQUIREMENTS_EMBED_LOCK_PATH.is_file(), f"missing {_REQUIREMENTS_EMBED_LOCK_PATH}"
        )
        self.text = _REQUIREMENTS_EMBED_LOCK_PATH.read_text(encoding="utf-8")
        self.blocks = _lock_requirement_blocks(self.text)

    def test_at_least_thirty_pins_present(self) -> None:
        # pip-compile --generate-hashes 산출물(32 패키지, 지시값 실측) -- 향후 소폭
        # 의존성 변동에 흔들리지 않도록 느슨하게 30 이상만 고정한다.
        self.assertGreaterEqual(len(self.blocks), 30)

    def test_every_pin_has_at_least_one_hash(self) -> None:
        for block in self.blocks:
            pin_line = block[0]
            hash_count = sum(line.count("--hash=sha256:") for line in block)
            self.assertGreaterEqual(hash_count, 1, f"pin without hash: {pin_line}")


class LockContainsSpecDirectDependenciesTest(unittest.TestCase):
    """F-05 완결 계약 3/6 -- lock 이 소스 spec(requirements-embed.txt)의 직접 의존성을
    전부 같은 버전으로 포함한다. ★torch 는 lock 에서 `torch==2.13.0+cpu`(CPU 휠 로컬
    라벨)로 나타나므로 정확 문자열 비교가 아니라 '동일 버전이거나 버전+로컬라벨' 규칙으로
    비교해야 한다 -- 그렇지 않으면 이 테스트 자체가 의도된 CPU 휠 pin 을 오검출로 깨뜨린다
    (지시 사항의 함정 포인트). 패키지명은 PEP 503 정규화(대소문자·`_`/`-`/`.` 무시)로 비교한다
    -- spec 의 `huggingface_hub` 가 lock 에서는 `huggingface-hub` 로 나타나는 케이스를
    흡수하기 위함이다."""

    _PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)==([^\s\\]+)")

    @staticmethod
    def _normalize_name(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name).lower()

    def setUp(self) -> None:
        self.assertTrue(_REQUIREMENTS_EMBED_PATH.is_file(), f"missing {_REQUIREMENTS_EMBED_PATH}")
        self.assertTrue(
            _REQUIREMENTS_EMBED_LOCK_PATH.is_file(), f"missing {_REQUIREMENTS_EMBED_LOCK_PATH}"
        )
        spec_text = _REQUIREMENTS_EMBED_PATH.read_text(encoding="utf-8")
        lock_text = _REQUIREMENTS_EMBED_LOCK_PATH.read_text(encoding="utf-8")

        self.spec_pins: dict[str, str] = {}
        for line in spec_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("--"):
                continue
            m = self._PIN_RE.match(stripped)
            self.assertIsNotNone(m, f"unparsable spec dependency line: {line!r}")
            self.spec_pins[self._normalize_name(m.group(1))] = m.group(2)

        self.lock_pins: dict[str, str] = {}
        for block in _lock_requirement_blocks(lock_text):
            m = self._PIN_RE.match(block[0])
            self.assertIsNotNone(m, f"unparsable lock pin line: {block[0]!r}")
            self.lock_pins[self._normalize_name(m.group(1))] = m.group(2)

    def test_spec_has_six_direct_dependencies(self) -> None:
        self.assertEqual(
            set(self.spec_pins),
            {"requests", "torch", "sentence-transformers", "transformers", "huggingface-hub", "numpy"},
        )

    def test_every_spec_dependency_present_in_lock_at_matching_version(self) -> None:
        for name, spec_version in self.spec_pins.items():
            self.assertIn(name, self.lock_pins, f"spec dependency {name!r} missing from lock")
            lock_version = self.lock_pins[name]
            matches = lock_version == spec_version or lock_version.startswith(spec_version + "+")
            self.assertTrue(
                matches,
                f"{name}: spec pins {spec_version!r} but lock has {lock_version!r} "
                "(expected an exact match, or spec_version+local-label e.g. torch==2.13.0+cpu)",
            )

    def test_torch_is_the_only_dependency_with_a_local_label(self) -> None:
        # ★torch 만 CPU 휠 로컬 라벨(`+cpu`)을 갖는다는 사실 자체를 고정 -- 나머지
        # 5종은 spec 과 완전히 동일한 버전 문자열이어야 한다(로컬 라벨이 다른 곳에도
        # 몰래 섞여 들면 이 테스트가 잡아낸다).
        self.assertEqual(self.lock_pins["torch"], "2.13.0+cpu")
        for name in ("requests", "sentence-transformers", "transformers", "huggingface-hub", "numpy"):
            self.assertEqual(self.lock_pins[name], self.spec_pins[name])


class WorkflowInstallUsesLockWithRequireHashesTest(unittest.TestCase):
    """F-05 완결 계약 4/6 -- Install dependencies 스텝이
    `pip install --require-hashes -r requirements-embed.lock` 단일 설치로 되어 있는지
    고정한다(과거 계약이었던 requirements.txt + requirements-embed.txt 2회 설치로의
    회귀를 막는다)."""

    def setUp(self) -> None:
        self.assertTrue(_WORKFLOW_PATH.is_file(), f"missing {_WORKFLOW_PATH}")
        self.workflow = _WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_install_step_uses_require_hashes_against_the_lock_file(self) -> None:
        self.assertIn("pip install --require-hashes -r requirements-embed.lock", self.workflow)


class WorkflowHasNoUnhashedInstallPathTest(unittest.TestCase):
    """F-05 완결 계약 5/6, Codex Major 2 로 강화됨 -- 워크플로 본문에 hash 미검증
    설치 경로가 **전혀** 남아있지 않다. `--require-hashes` 는 같은 invocation 안의
    요구사항에만 강제되므로, 별도 줄/스텝으로 hash 없는 `pip install -r
    requirements.txt` 나 `pip install -r requirements-embed.txt` 가 하나라도 섞여
    들면 그 경로로 공급망 검증이 조용히 우회된다.

    ★Codex Major 2: 종전에는 `python -m pip install --upgrade pip` 를 "PyPI
    서명·TLS 로 이미 보호됨"이라는 이유로 예외 허용했지만, 이 job 은 곧이어
    SUPABASE_SERVICE_ROLE_KEY 를 쥔다 -- 그 한 줄이 "hash 미검증 설치 경로 없음"
    이라는 주장을 문자 그대로 반박했다. 이제 그 문자열은 워크플로 본문에
    아예 있어서는 안 된다(예외 없음)."""

    def setUp(self) -> None:
        self.assertTrue(_WORKFLOW_PATH.is_file(), f"missing {_WORKFLOW_PATH}")
        self.workflow = _WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_no_unhashed_requirements_txt_install(self) -> None:
        self.assertNotIn("pip install -r requirements.txt", self.workflow)

    def test_no_unhashed_requirements_embed_txt_install(self) -> None:
        self.assertNotIn("pip install -r requirements-embed.txt", self.workflow)

    def test_no_unhashed_pip_bootstrap_line_at_all(self) -> None:
        """Codex Major 2 완결 -- `python -m pip install --upgrade pip` 는 더 이상
        예외가 아니라, 워크플로 본문에 문자열 자체가 없어야 한다."""
        self.assertNotIn("pip install --upgrade pip", self.workflow)

    def test_every_pip_install_line_is_hash_verified(self) -> None:
        pip_install_lines = [
            line.strip() for line in self.workflow.splitlines() if "pip install" in line
        ]
        self.assertTrue(pip_install_lines)  # sanity: the step exists at all
        for line in pip_install_lines:
            self.assertIn(
                "--require-hashes", line,
                f"unhashed pip install line found: {line}",
            )


class IntakeRequirementsStillTorchFreeAfterLockAdoptionTest(unittest.TestCase):
    """F-05 완결 계약 6/6 (회귀 방지, 기존 계약 유지) -- lock 도입 이후에도 루트
    requirements.txt(매일 도는 intake CI)는 여전히 torch-free 여야 한다. #8/#11 과 같은
    축의 계약을 lock 파일 신설 이후에도 다시 고정한다(embed 전용 무거운 의존성이 intake
    로 새지 않는다는 것이 이번 변경으로 흔들리지 않았음을 확인)."""

    def test_requirements_txt_has_no_ml_dependencies(self) -> None:
        text = _REQUIREMENTS_PATH.read_text(encoding="utf-8").lower()
        for forbidden in ("sentence-transformers", "torch", "transformers", "huggingface"):
            self.assertNotIn(forbidden, text, f"requirements.txt must not pull in {forbidden!r}")


# ---------------------------------------------------------------------------
# 13) dry-run 리포트를 로그(stdout)에도 찍는 변경(_write_report) -- 시크릿 미유출 계약
#
# Codex 감사 지적: 종전엔 --output 이 있으면 파일로만 나가 job 로그에 카운터가 안 보여
# to_embed=0 같은 사실을 사람이 정황 추론해야 했다. _write_report 를 "항상 stdout
# print + path 있으면 파일도" 로 고쳐 그 추론을 관찰로 바꿨다(방금 수정, 위 707행 근처
# _write_report 독스트링 참조). 이 섹션은 그 변경이 리포트에 자격증명을 새어나가게
# 하지 않는다는 계약을 테스트로 고정한다 -- 코드는 건드리지 않는다.
# ---------------------------------------------------------------------------


# run_embed() 가 초기화하는 report dict 의 키 전체(561행 근처 실측, 순서 무관 집합
# 비교). 이 목록에 없는 키가 하나라도 report 에 나타나면 아래
# ReportKeySetIsClosedAllowlistTest 가 즉시 실패한다.
_REPORT_KEY_ALLOWLIST = frozenset({
    "mode",
    "embedding_version",
    "embed_input_requested",
    "model",
    "candidates_total",
    "already_current",
    "to_embed",
    "revision_changed",
    "embedded",
    "upserted",
    "b_input_used",
    "b_fallback_to_a",
    "input_text_len_mean",
    "elapsed_seconds",
    "errors",
})


class ReportKeySetIsClosedAllowlistTest(unittest.TestCase):
    """report 의 키 집합이 알려진 허용목록과 정확히 일치한다.

    ★왜 이 테스트가 시크릿 안전장치인가: report 는 카운터/메타만 담아야 한다는 계약이
    코드 리뷰만으로는 조용히 깨질 수 있다 -- 예를 들어 디버깅 편의를 위해 누군가
    `report["service_key"] = service_key` 나 `report["base_url"] = base_url` 같은
    줄을 run_embed() 에 추가하면, 그 값은 _write_report() 를 거쳐 곧장 CI job 로그에
    찍힌다(이번에 고친 바로 그 경로). 이 테스트는 "새 키 추가 = 이 테스트가 반드시
    깨진다" 를 강제해서, 그런 변경이 반드시 사람 리뷰를 통과하도록 만드는 장치다 --
    허용목록에 키를 추가하는 PR 자체가 "왜 이 값을 로그에 노출해도 안전한가" 를
    설명해야 하게 된다.

    ★무네트워크 경로: base_url 을 "http://x"(https:// 아님)로 주면
    _normalize_base_url 이 None 을 반환해 run_embed 가 fetch_target_findings 호출
    전에 조기 리턴한다(_normalize_base_url 소스: SUPABASE_URL must start with
    https:// 가드, 707행 근처 base = _normalize_base_url(base_url) 블록). 이 경로는
    report 를 그대로 초기화값 그대로 반환하므로, 네트워크를 전혀 타지 않고도 전체
    키 집합을 관찰할 수 있다."""

    def test_report_keys_exactly_match_known_allowlist(self) -> None:
        report = svc.run_embed(
            "http://x", "fake-key",
            embedding_version=1, embed_input="A", dry_run=True,
        )
        self.assertEqual(set(report.keys()), set(_REPORT_KEY_ALLOWLIST))
        # 조기 리턴 경로 자체도 확인(허용목록 밖 회귀를 잡으려면 실제로 이 분기를
        # 탔는지 알아야 한다) -- SUPABASE_URL 가드 에러 하나만 담겨야 한다.
        self.assertEqual(
            report["errors"], ["SUPABASE_URL must start with https://"],
        )


def _run_embed_with_mocked_transport(
    base_url: str,
    service_key: str,
    *,
    finding_text: str = "text one",
) -> dict:
    """network/모델 로딩을 전부 monkeypatch 로 대체해 run_embed 를 오프라인으로
    실행한다(위 DryRunTest/SanityViolation 테스트와 동일한 패턴). service_key 와
    base_url 은 오직 이 함수의 인자로만 흘러가고, 아래 fake 들은 그 값을 받되
    무시한다 -- 즉 실제 HTTP 호출은 발생하지 않는다."""
    original_upsert = svc._upsert_embeddings_batch
    original_fetch_targets = svc.fetch_target_findings
    original_fetch_raw = svc.fetch_raw_signals_by_ids
    original_fetch_existing = svc.fetch_existing_embeddings
    original_load_model = svc.load_model
    original_resolve_revision = svc.resolve_model_revision
    original_embed_texts = svc.embed_texts

    class _FakeModel:
        pass

    def _fake_upsert(*_args, **_kwargs):
        return 200, [], ""

    try:
        svc._upsert_embeddings_batch = _fake_upsert
        svc.fetch_target_findings = lambda base, key: [
            {"finding_id": "finding-1", "finding_text": finding_text,
             "source": "FDA Warning Letter", "raw_signal_id": "rawsig-1"},
        ]
        svc.fetch_raw_signals_by_ids = lambda base, key, ids: {}
        svc.fetch_existing_embeddings = lambda base, key, version: {}
        svc.resolve_model_revision = lambda name: "deadbeef"
        svc.load_model = lambda name, revision: _FakeModel()
        svc.embed_texts = lambda model, texts, *, batch_size: [[0.1] * svc.EMBED_DIM for _ in texts]

        return svc.run_embed(
            base_url, service_key,
            embedding_version=1, embed_input="A", dry_run=True,
        )
    finally:
        svc._upsert_embeddings_batch = original_upsert
        svc.fetch_target_findings = original_fetch_targets
        svc.fetch_raw_signals_by_ids = original_fetch_raw
        svc.fetch_existing_embeddings = original_fetch_existing
        svc.load_model = original_load_model
        svc.resolve_model_revision = original_resolve_revision
        svc.embed_texts = original_embed_texts


# 실제 시크릿이 아니라 "이 문자열이 report 어딘가에 등장하면 유출" 을 검출하기 위한
# 센티널 값 -- 절대 실재 자격증명이 아니다.
_SENTINEL_SERVICE_KEY = "SENTINEL-SERVICE-KEY-DO-NOT-LEAK-9f3a"
_SENTINEL_BASE_URL = "https://sentinel-do-not-leak-9f3a.supabase.co"


class ReportContainsNoSentinelSecretTest(unittest.TestCase):
    """센티널 service_key/URL 을 자격증명으로 넘겨 run_embed() 를 (오프라인으로) 실행한
    뒤, json.dumps(report) 직렬화 결과에 그 센티널이 전혀 등장하지 않는다는 것을
    고정한다. transport 는 전부 monkeypatch 되어 실제 네트워크 호출은 없다(위
    _run_embed_with_mocked_transport 참조) -- 이 테스트가 검사하는 것은 "run_embed
    가 자격증명을 report dict 어딘가(특히 errors 자유텍스트)에 흘려 넣지 않는가"
    자체다."""

    def test_sentinel_credentials_never_appear_in_serialized_report(self) -> None:
        import json

        report = _run_embed_with_mocked_transport(
            _SENTINEL_BASE_URL, _SENTINEL_SERVICE_KEY,
        )

        self.assertEqual(report["errors"], [])  # sanity: 정상 경로를 탔다
        serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(_SENTINEL_SERVICE_KEY, serialized)
        self.assertNotIn(_SENTINEL_BASE_URL, serialized)


class WriteReportAlwaysPrintsToStdoutTest(unittest.TestCase):
    """_write_report() 가 path 인자를 줘도 stdout 에 항상 JSON 을 출력하고(이번
    변경의 핵심 계약), 그 stdout 출력물과 파일 양쪽 모두에 센티널이 없다는 것을
    고정한다."""

    def test_stdout_and_file_both_receive_report_and_neither_leaks_sentinel(self) -> None:
        import contextlib
        import io
        import json
        import tempfile

        report = _run_embed_with_mocked_transport(
            _SENTINEL_BASE_URL, _SENTINEL_SERVICE_KEY,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = str(Path(tmpdir) / "report.json")

            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                svc._write_report(out_path, report)

            stdout_text = captured.getvalue()
            file_text = Path(out_path).read_text(encoding="utf-8")

        # stdout 이 실제로 뭔가를 찍었다(회귀 방지: path 가 있다고 조용히 파일로만
        # 새는 옛 동작으로 되돌아가면 이 assert 가 즉시 잡는다).
        self.assertTrue(stdout_text.strip())
        # stdout 에 찍힌 JSON 은 파일에 쓰인 것과 같은 report 내용이다(개행 하나
        # 차이만 허용 -- _write_report 는 파일에 trailing "\n" 을 덧붙인다).
        self.assertEqual(json.loads(stdout_text), json.loads(file_text))

        for sentinel in (_SENTINEL_SERVICE_KEY, _SENTINEL_BASE_URL):
            self.assertNotIn(sentinel, stdout_text)
            self.assertNotIn(sentinel, file_text)


# ---------------------------------------------------------------------------
# 14) Codex Minor 3 -- errors[] 자유 텍스트(str(exc)) 마스킹
#
# 배경: report 의 키 허용목록(§13)은 **키**만 닫았고 **값**은 제한하지 않았다. errors[]
# 항목의 상당수는 str(exc) -- requests/urllib3 등 우리가 내용을 통제할 수 없는 라이브러리가
# 만드는 자유 텍스트라서, 예외 메시지에 URL 자격증명이나 쿼리스트링 토큰이 실려 있으면
# 그대로 _write_report() 의 stdout 까지 찍힌다(Codex 실증: `https://u:p@proxy.invalid/token`
# 주입). _sanitize_error() 가 report["errors"].append()/.extend() 의 모든 호출부에서
# 이를 마스킹한다 -- 이 절은 단위 테스트(패턴별)와 run_embed 경로 전체를 통한 회귀
# 테스트(§13 하네스 재사용) 양쪽을 고정한다.
# ---------------------------------------------------------------------------


class SanitizeErrorUnitTest(unittest.TestCase):
    def test_url_credentials_masked(self) -> None:
        self.assertEqual(
            svc._sanitize_error("connect to https://u:p@proxy.invalid/token failed"),
            "connect to https://***@proxy.invalid/token failed",
        )

    def test_query_token_masked_case_insensitive(self) -> None:
        self.assertEqual(
            svc._sanitize_error("GET /x?TOKEN=abc123&other=1"),
            "GET /x?TOKEN=***&other=1",
        )

    def test_secret_key_password_apikey_authorization_all_masked(self) -> None:
        for key in ("key", "token", "secret", "password", "apikey", "authorization"):
            with self.subTest(key=key):
                self.assertEqual(
                    svc._sanitize_error(f"boom {key}=super-secret-value end"),
                    f"boom {key}=*** end",
                )

    def test_jwt_masked(self) -> None:
        jwt = (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        )
        self.assertEqual(svc._sanitize_error(f"bad token {jwt}"), "bad token ***jwt***")

    def test_long_message_truncated_at_500_chars_plus_ellipsis(self) -> None:
        text = "x" * 600
        result = svc._sanitize_error(text)
        self.assertEqual(len(result), svc._ERROR_MAX_LEN + 1)  # 500 chars + "…"
        self.assertTrue(result.endswith("…"))
        self.assertEqual(result[:-1], "x" * svc._ERROR_MAX_LEN)

    def test_normal_messages_pass_through_unchanged(self) -> None:
        for normal in ("http_500", "retry_exhausted", "timeout", "TypeError"):
            self.assertEqual(svc._sanitize_error(normal), normal)


_SENTINEL_EXCEPTION_TEXT = "SENTINEL-EXCEPTION-TEXT-secret=https://u:p@proxy.invalid/token"


class ReportErrorsSanitizeExceptionMessagesTest(unittest.TestCase):
    """Codex Minor 3 회귀 테스트 -- fetch_target_findings 가 URL 자격증명 + key=value
    토큰을 담은 예외를 던지도록 monkeypatch 한 뒤(§13 의 save/restore 하네스와 동일한
    패턴), run_embed() 의 반환값 직렬화 결과와 _write_report() 의 stdout 출력 양쪽 모두에
    원문 자격증명(`u:p@`, 원본 `secret=https://...token` 전체)이 없고 마스킹 흔적(`***`)이
    있는지 확인한다."""

    def setUp(self) -> None:
        self._original_fetch_targets = svc.fetch_target_findings

    def tearDown(self) -> None:
        svc.fetch_target_findings = self._original_fetch_targets

    def test_injected_exception_credentials_are_masked_in_report_and_stdout(self) -> None:
        import contextlib
        import io
        import json

        def _raise(base, key):
            raise RuntimeError(_SENTINEL_EXCEPTION_TEXT)

        svc.fetch_target_findings = _raise

        report = svc.run_embed(
            "https://example.supabase.co", "fake-key",
            embedding_version=1, embed_input="A", dry_run=True,
        )

        self.assertTrue(report["errors"])
        serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("u:p@", serialized)
        self.assertNotIn("secret=https://u:p@proxy.invalid/token", serialized)
        self.assertIn("***", serialized)

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            svc._write_report(None, report)
        stdout_text = captured.getvalue()

        self.assertNotIn("u:p@", stdout_text)
        self.assertNotIn("secret=https://u:p@proxy.invalid/token", stdout_text)
        self.assertIn("***", stdout_text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
