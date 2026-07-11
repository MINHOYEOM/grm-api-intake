-- FIND-1 M3a Supabase(Postgres) 스키마 — grm-findings.sqlite3 sidecar와 컬럼/제약 의미 동치.
-- 단일 소스: findings_supabase.postgres_schema_ddl() 출력이 이 파일과 byte 일치해야 한다.
-- 이 단계(M3a)는 스키마만 생성한다. 데이터 적재는 컨트롤 타워가 Supabase MCP로 별도 실행한다.

-- raw_signals: 재추출 가능한 원본 보존층. raw_json/row_json 은 jsonb 가 아니라 text —
-- canonical JSON byte 를 그대로 보존해 raw_sha256 재검증이 항상 가능해야 한다.
create table if not exists public.raw_signals (
  schema_version text not null check (schema_version = 'grm-raw-signal/v1'),
  raw_signal_id text primary key,
  source text not null,
  source_kind text not null,
  document_id text not null,
  published_date text not null,
  collected_at text,
  title text not null,
  firm_name text,
  site_name text,
  site_country text,
  modality text,
  source_url text,
  official_url text,
  raw_sha256 text not null check (char_length(raw_sha256) = 64),
  raw_json text not null,
  row_json text not null,
  extraction_status text not null,
  ingested_at timestamptz not null default now(),
  unique (source, document_id)
);

-- RLS 활성화(정책은 이 파일 하단에서 두 테이블을 함께 전면 차단한다)
alter table public.raw_signals enable row level security;

-- findings: 지적사항 분석층. inspector_names/cfr_refs/mfds_refs 는 SQLite TEXT 배열의 jsonb 강화형.
create table if not exists public.findings (
  schema_version text not null check (schema_version = 'grm-finding/v1'),
  taxonomy_version text not null check (taxonomy_version in ('grm-finding-taxonomy/v1', 'grm-finding-taxonomy/v2', 'grm-finding-taxonomy/v3', 'grm-finding-taxonomy/v4')),
  finding_id text primary key,
  raw_signal_id text not null references public.raw_signals (raw_signal_id) on delete cascade,
  source text not null,
  agency text not null,
  document_type text not null,
  document_id text not null,
  published_date text not null,
  firm_name text not null,
  entity_id text,
  site_name text,
  site_country text,
  product_family text,
  modality text,
  category_code text not null check (category_code in ('data_integrity', 'computer_system_validation', 'documentation_records', 'aseptic_sterility_assurance', 'environmental_monitoring', 'cleaning_validation', 'complaint_recall', 'deviation_capa', 'quality_unit_oversight', 'qc_lab_controls', 'process_validation', 'equipment_facility', 'material_supplier_control', 'contamination_control', 'validation_qualification', 'stability_storage', 'labeling_packaging', 'regulatory_reporting', 'training_personnel', 'other_quality_system')),
  category_label_ko text not null,
  finding_text text not null,
  finding_language text,
  evidence_level text not null check (evidence_level in ('A', 'B', 'C')),
  evidence_url text not null,
  inspector_names jsonb not null default '[]'::jsonb,
  cfr_refs jsonb not null default '[]'::jsonb,
  mfds_refs jsonb not null default '[]'::jsonb,
  extraction_method text not null check (extraction_method in ('deterministic', 'llm_assisted', 'manual')),
  confidence double precision not null check (confidence >= 0 and confidence <= 1),
  review_status text not null check (review_status in ('accepted', 'needs_review', 'rejected')),
  finding_text_ko text not null default '',
  translation_method text not null default '' check (translation_method in ('', 'llm_assisted', 'manual')),
  ingested_at timestamptz not null default now()
);

-- SQLite 의 UNIQUE(raw_signal_id, finding_text) 를 그대로 옮기면 finding_text 가 길 때
-- btree 인덱스 행 크기 한계(~2704B)를 넘을 수 있어, md5 해시 인덱스로 대체한다.
create unique index if not exists findings_rawsig_text_md5_uq
  on public.findings (raw_signal_id, md5(finding_text));

-- 조회 인덱스(SQLite idx_findings_facets/idx_findings_firm 과 동일 취지)
create index if not exists idx_findings_facets
  on public.findings (agency, category_code, modality, published_date);
create index if not exists idx_findings_firm
  on public.findings (firm_name, published_date);

-- RLS 활성화(정책은 바로 아래에서 두 테이블을 함께 전면 차단한다)
alter table public.findings enable row level security;

-- 정책 0개로 전면 차단 + anon/authenticated 권한 회수: M3a 는 service_role 전용 적재/조회만 허용한다.
-- 웹 공개(anon 읽기) 정책은 M3 후속 단계에서 별도로 추가한다.
revoke all on public.raw_signals, public.findings from anon, authenticated;
