# 11월 21일 KR 직접 유입 급증 분석

11월 21일의 KR 직접 유입 580회는 요청에 제공된 수치다. 그날의 전체 방문, 국가·출처별 합계와 표본 간격, 직전 28일 중앙값, 시간대별 분포는 확인하지 않았다. 저장소의 스키마·수집 코드·시험 자료만 사용했다. 이 사례의 자동 접속 여부와 기존 필터 적용 여부는 미확인이다.

## 기존 필터가 잡는 범위

- 139의 `rum_anomaly_days`는 일 방문 200회 이상이면서 직전 28일 중앙값의 8배 이상인 날을 통째로 제외한다. 직전 관측일이 7일 이상이어야 하고, `rum_anomaly_overrides.excluded`의 수동 포함·제외가 우선한다. 가령 전체 일 방문도 580회이고 충분한 이력이 있다면, 수동 판정이 없을 때 중앙값 72.5회 이하에서 이미 걸린다.
- 139의 `rum_anomaly_paths`는 제외일 이외에 경로별 일 방문이 100회 이상이면서 직전 28일 경로 중앙값을 올림한 값(최소 1)의 20배 이상이면 그 경로를 기준값으로 제한한다. 경로의 초과분은 `rum_daily_clean.value`에서도 차감된다. 국가·출처·기기·출처×경로 보기는 날짜 제외만 공유한다. 155의 탐색·UI 보기도 같은 날짜를 제외한다. 161은 보고 함수의 기존 `_clean` 참조를 유지한다.
- 두 규칙에는 국가나 유입 출처 조건이 없다. 전체 방문이 8배에 못 미치고 경로마다 경로 규칙에 못 미치면, 한 국가의 직접 유입이 커도 통과할 수 있다. 139가 설명한 11월 1일은 여러 국가에 걸친 사례다. 국가는 봇 자체를 식별하는 신호로 쓰기 어렵다.

## 현재 열로 판단할 수 있는 범위

| 근거 | 판단 범위 |
| --- | --- |
| `rum_daily(snap_date, metric, value, sample_interval)` | `metric = 'visits'`의 UTC 일 방문과 일 단위 기준선. UTC 기준은 075의 정정 및 현재 수집 코드 기준이다. |
| `rum_country_daily(snap_date, country, visits, sample_interval)`, `rum_referrer_daily(snap_date, referer_host, visits, sample_interval)` | 같은 날의 국가별·출처별 개별 합계. 077의 보고 함수가 사용하는 `KR` 코드와 수집기의 빈 출처 표기 `(direct)`를 사용한다. 빈 출처는 북마크·메신저 앱 등 정상 유입에서도 생길 수 있다. |
| `rum_path_daily`, `rum_referrer_path_daily`, `rum_device_daily` | 각각 일별 경로, 출처×경로, 기기 유형. 출처×경로 시험 자료도 날짜·출처·경로 조합까지만 보여 준다. 날짜로 조인해 특정 국가의 경로나 기기를 복원할 수 없다. |
| `rum_referrer_path_runs.window_start/window_end` | 수집 요청의 범위와 적재 품질 기록. 방문 발생 시각은 저장하지 않는다. 분·시간 단위 폭주나 방문자별 반복 여부를 판단하려면 별도의 시간대별 또는 결합 집계가 필요하다. |

전체 방문이 `T`, KR 방문이 `K`, 직접 방문이 `D`일 때, 세 집계가 같은 방문 모집단을 센다는 전제에서 KR 직접 유입은 최소 `max(0, K + D - T)`회이고 최대 `min(K, D)`회다. 국가·출처 합이 각각 전체와 같아도 같은 수집 창과 모집단을 보장하지는 않는다. 수집기는 국가 20개·출처 25개 상위 행을 남기고, 그룹마다 표본 정밀도에 따라 기존 날짜를 별도로 보존할 수 있다. 표본 간격 1과 합계 일치는 후보 산출의 최소 조건이다. 집계는 완료된 UTC 날짜로 비교하고 혼합 적재나 날짜 변환 가능성을 검토한다.

## 검토 후보 규칙

1. 같은 UTC 날짜의 전체·국가·출처 행에서 `sample_interval = 1`인 경우만 평가한다. 국가 합계와 출처 합계는 각각 전체 방문과 같아야 한다. 누락, 표본 추정, 정밀도 미상, 합계 불일치가 있으면 후보를 내지 않는다.
2. KR 직접 유입의 하한이 500회 이상이고 전체의 90% 이상이어야 한다.
3. 직전 28일에 원본 일 방문 데이터가 7일 이상 있고 현재 전체 방문이 그 중앙값의 4배 이상이어야 한다. 이전 날짜의 합계는 139와 같이 원본에서 읽으며, 과거 표본 추정값과 과거 급증일도 기준선에 포함될 수 있다.

500회·90%·4배는 오프라인 검토용 가설이다. 예를 들어 전체 600회, KR 560회, 직접 580회, 기준선 150회라면 최소 교집합은 540회(90%)이고 기준선의 4배라 후보가 된다. 580회는 요청에서 가져온 수치이며, 이 예의 나머지 숫자는 합성 시험 자료다. 같은 조건에서도 KR이 559회면 교집합 하한 539회로 후보에서 빠진다. 뉴스레터·공유 직후 정상 유입도 집중될 수 있고, 낮은 비율로 섞인 봇은 이 규칙을 통과할 수 있다.

파이썬 함수와 SQL 초안은 후보만 반환한다. 검토자가 전체 날짜 제외를 승인하면 현재의 수동 판정 구조로 처리할 수 있다. 날짜 제외는 정상 방문과 해당 날짜의 탐색·UI 집계에도 영향을 준다. 국가별·출처별 주변 합계에서 특정 방문을 빼면 집계 간 정합성을 확인할 수 없다. KR 직접 유입만 제거하려면 국가×출처 결합 집계와 영향을 받는 경로·기기 등 보고 차원의 일관된 차감 기준이 먼저 필요하다.

## 필요한 SQL 초안

기존 컬럼으로 후보를 뽑는 읽기 전용 쿼리다. SQL 실행과 운영 검증은 하지 않았다. 입력 조건을 검증하기 전에는 보고 보기와 연결하지 않는다. 검증 후 자동 제외를 승인한다면 `rum_anomaly_days`의 날짜 판정에 합치되 수동 판정을 우선하고, 기존 보기의 열·권한과 RPC 반환 구조를 유지한다. 자동 사유와 `rum_anomaly_summary`의 기준 설명에도 새 조건을 반영한다.

```sql
with candidates as (
  select d.snap_date, d.value as visits, h.baseline, h.baseline_days,
         greatest(coalesce(c.kr_visits, 0) + coalesce(r.direct_visits, 0) - d.value, 0)
           as kr_direct_min
  from public.rum_daily d
  cross join lateral (
    select sum(visits) as total,
           sum(visits) filter (where country = 'KR') as kr_visits,
           bool_and(sample_interval is not distinct from 1) as full_sample
    from public.rum_country_daily
    where snap_date = d.snap_date
  ) c
  cross join lateral (
    select sum(visits) as total,
           sum(visits) filter (where referer_host = '(direct)') as direct_visits,
           bool_and(sample_interval is not distinct from 1) as full_sample
    from public.rum_referrer_daily
    where snap_date = d.snap_date
  ) r
  cross join lateral (
    select percentile_cont(0.5) within group (order by prev.value) as baseline,
           count(*) as baseline_days
    from public.rum_daily prev
    where prev.metric = 'visits'
      and prev.snap_date between d.snap_date - 28 and d.snap_date - 1
  ) h
  where d.metric = 'visits'
    and d.value >= 500
    and d.sample_interval = 1
    and c.full_sample and r.full_sample
    and c.total = d.value and r.total = d.value
    and h.baseline_days >= 7 and d.value >= 4 * h.baseline
)
select c.snap_date, c.visits, c.baseline, c.baseline_days, c.kr_direct_min,
       a.source as existing_exclusion, o.excluded as manual_exclusion
from candidates c
left join public.rum_anomaly_days a using (snap_date)
left join public.rum_anomaly_overrides o using (snap_date)
where c.kr_direct_min >= 500
  and 10 * c.kr_direct_min >= 9::bigint * c.visits
order by c.snap_date;
```
