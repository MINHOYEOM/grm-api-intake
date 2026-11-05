-- 지식그래프 — 지적↔제품군 엣지 finding_product. 가산만(테이블 1, 행 쓰기 없음, 기존 RPC·테이블 불변).
--
-- 배경: findings.modality / product_family 는 문서 단위 값을 복사한 열이라 지적 28,414건 중 33건(0.1%)만 차 있다.
-- 그 열은 건드리지 않는다. 이 테이블은 **지적 본문·제품명에 제품군이 명시된 경우에만** 지적 단위로 잇는다.
--   · 합성 의약품 캐치올 금지 — 근거가 없으면 행이 없다(문서/업체 값을 물려주지 않는다).
--   · axis='modality'  : value in ('Chemical','Biologic')   (grm_taxonomy 와 같은 어휘, 'Other'·'' 는 저장하지 않음)
--     axis='dosage_form': value in ('tablet','capsule','syrup','ointment','injection')
--   · confidence >= 0.8 만 저장(finding_term 과 같은 관례). basis = 규칙 이름, evidence = 근거 조각(<=120자).
--   · 규칙·근거 범위는 kg_product.py 독스트링이 정본: MFDS 회수/행정처분 제품명 + FDA 483 본문만.
--     WL·HC·EU·MHRA 는 본문/필드에 제품 정보가 없거나 상투구 오탐이 커서 연결하지 않는다.
-- 채우기: 이 파일이 아니라 `python kg_product.py --from-db --emit-sql out.sql` 이 만든 INSERT 배치(멱등)로 한다.
-- 갱신: 규칙이 바뀌면 해당 basis 행을 지우고 다시 채운다.

create table if not exists public.finding_product (
  finding_id text not null references public.findings (finding_id) on delete cascade,
  axis       text not null check (axis in ('modality', 'dosage_form')),
  value      text not null,
  confidence numeric(3,2) not null check (confidence >= 0.8 and confidence <= 1),
  basis      text not null,
  evidence   text not null default '',
  primary key (finding_id, axis, value),
  check (
    (axis = 'modality'    and value in ('Chemical', 'Biologic')) or
    (axis = 'dosage_form' and value in ('tablet', 'capsule', 'syrup', 'ointment', 'injection'))
  )
);
create index if not exists idx_finding_product_axis_value on public.finding_product (axis, value);
alter table public.finding_product enable row level security;
revoke all on table public.finding_product from public, anon, authenticated;
