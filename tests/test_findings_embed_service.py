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
import unittest
from pathlib import Path

import findings_embed_service as svc


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_EMBEDDINGS_PATH = _MIGRATIONS_DIR / "019_findings_embeddings.sql"
_SIMILAR_LEXICAL_PATH = _MIGRATIONS_DIR / "018_findings_similar_lexical.sql"
_REQUIREMENTS_PATH = Path(__file__).resolve().parent.parent / "requirements.txt"


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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
