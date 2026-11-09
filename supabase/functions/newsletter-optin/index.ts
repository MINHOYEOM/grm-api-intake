import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { envAny, jsonResponse, optionsResponse, readJsonBody } from "../_shared/admin.ts";

// 회원 뉴스레터 구독(옵트인) — 이메일 확인이 끝난 로그인 회원이 **본인 이메일만** 구독 리스트에 올린다.
//
// 왜 서버인가: 브라우저가 임의 이메일을 Brevo 에 등록할 수 있으면 남의 주소를 구독시킬 수 있다.
// 이 함수는 ① 세션 JWT 로 본인임을 확인하고 ② 이메일 확인(email_confirmed_at)이 끝난 계정만
// 받으며 ③ 등록할 이메일을 요청 본문이 아니라 **JWT 의 이메일**에서만 읽는다.
//
// 원칙
//  - 동의 근거는 서버 시각으로 user_metadata.newsletter_consent 와 Brevo 연락처 속성
//    (SOURCE·CONSENT_AT·CONSENT_VER)에 남긴다. 속성이 Brevo 에 없으면 속성 없이 재시도한다
//    (그 경우 근거는 user_metadata 에만 남고 응답 attrs=false).
//  - 이미 수신거부한 연락처(emailBlacklisted)는 되살리지 않는다(blocked_unsubscribed).
//  - 응답·로그·감사 기록에 이메일을 남기지 않는다(상태 문자열과 불리언만).
//  - 영문 리스트 ID(GRM_NEWSLETTER_LIST_ID_EN)가 없으면 한국어 리스트에 등록한다(list=ko_fallback).
//  - 멱등: 같은 요청을 되풀이해도 결과는 같다. 직접 쓰기는 Brevo 연락처 upsert·본인 user_metadata 뿐.

const SOURCES = new Set(["signup-optin", "login-nudge"]);
const VER_RE = /^[a-z0-9-]{1,24}$/;

function apiKey(): string {
  return envAny(["BREVO_API_KEY", "NEWSLETTER_API_KEY"]);
}

async function brevo(path: string, init: RequestInit = {}) {
  const res = await fetch(`https://api.brevo.com/v3${path}`, {
    ...init,
    headers: {
      "Accept": "application/json",
      "Content-Type": "application/json",
      "api-key": apiKey(),
      ...(init.headers || {}),
    },
  });
  const text = await res.text();
  let payload: Record<string, unknown> = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (_) {
      payload = {};
    }
  }
  return { ok: res.ok, status: res.status, payload };
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return optionsResponse();
  if (req.method !== "POST") return jsonResponse({ error: "method_not_allowed" }, 405);

  const token = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "").trim();
  if (!token) return jsonResponse({ error: "missing_auth" }, 401);

  const supabaseUrl = envAny(["SUPABASE_URL"]);
  const serviceRoleKey = envAny(["SUPABASE_SERVICE_ROLE_KEY"]);
  if (!supabaseUrl || !serviceRoleKey || !apiKey()) return jsonResponse({ error: "server_not_configured" }, 500);
  const listKo = envAny(["BREVO_LIST_ID", "GRM_NEWSLETTER_LIST_ID"]);
  const listEn = envAny(["GRM_NEWSLETTER_LIST_ID_EN"]);
  if (!listKo) return jsonResponse({ error: "server_not_configured" }, 500);

  const supabase = createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data: userData, error: userError } = await supabase.auth.getUser(token);
  const user = userData?.user;
  if (userError || !user) return jsonResponse({ error: "invalid_session" }, 401);

  const email = String(user.email || "").trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return jsonResponse({ error: "no_email" }, 400);
  // 이메일 소유가 확인되지 않은 계정은 등록하지 않는다(남의 주소로 가입한 경우 차단).
  if (!user.email_confirmed_at) return jsonResponse({ error: "email_not_confirmed" }, 403);

  const body = await readJsonBody(req);

  // 읽기 전용 상태 조회 — 로그인 후 안내(login-nudge)를 띄울지 판단용. 쓰기 0.
  // 응답은 본인 JWT 이메일에 대한 상태 문자열뿐(subscribed | blocked_unsubscribed | none).
  if (String(body.action || "") === "status") {
    const st = await brevo(`/contacts/${encodeURIComponent(email)}`);
    if (st.status === 404) return jsonResponse({ ok: true, status: "none" });
    if (!st.ok) return jsonResponse({ error: "brevo_request_failed" }, 502);
    if (st.payload.emailBlacklisted === true) return jsonResponse({ ok: true, status: "blocked_unsubscribed" });
    const ids = Array.isArray(st.payload.listIds) ? (st.payload.listIds as unknown[]).map(Number) : [];
    const mine = [listKo, listEn].filter(Boolean).map(Number);
    return jsonResponse({ ok: true, status: ids.some((i) => mine.includes(i)) ? "subscribed" : "none" });
  }

  const source = String(body.source || "");
  const ver = String(body.consent_ver || "");
  const lang = String(body.lang || "ko") === "en" ? "en" : "ko";
  if (!SOURCES.has(source) || !VER_RE.test(ver)) return jsonResponse({ error: "bad_request" }, 400);

  const meta = (user.user_metadata || {}) as Record<string, unknown>;
  // 가입 경로는 가입 화면에서 체크한 기록(signUp 의 user_metadata)이 있어야 한다.
  // 안내 카드 경로는 사용자가 버튼을 직접 누른 요청 자체가 동의다.
  if (source === "signup-optin" && meta.newsletter_optin !== true) {
    return jsonResponse({ error: "no_optin_recorded" }, 403);
  }

  const useEn = lang === "en" && !!listEn;
  const listId = Number(useEn ? listEn : listKo);
  const listTag = lang === "en" && !listEn ? "ko_fallback" : (useEn ? "en" : "ko");
  if (!Number.isFinite(listId)) return jsonResponse({ error: "server_not_configured" }, 500);

  // 이미 수신거부한 연락처는 존중한다 — 되살리려면 구독 밴드 폼(더블 옵트인)을 거쳐야 한다.
  const existing = await brevo(`/contacts/${encodeURIComponent(email)}`);
  if (existing.ok && existing.payload.emailBlacklisted === true) {
    return jsonResponse({ ok: true, status: "blocked_unsubscribed", list: listTag });
  }
  if (!existing.ok && existing.status !== 404) return jsonResponse({ error: "brevo_request_failed" }, 502);

  const at = new Date().toISOString();
  const create = (withAttrs: boolean) =>
    brevo("/contacts", {
      method: "POST",
      body: JSON.stringify({
        email,
        listIds: [listId],
        updateEnabled: true,
        ...(withAttrs ? { attributes: { SOURCE: source, CONSENT_AT: at, CONSENT_VER: ver } } : {}),
      }),
    });
  let res = await create(true);
  let attrs = true;
  if (res.status === 400) {
    // Brevo 에 연락처 속성(SOURCE·CONSENT_AT·CONSENT_VER)이 아직 없을 때 — 속성 없이 재시도.
    res = await create(false);
    attrs = false;
  }
  if (!res.ok) return jsonResponse({ error: "brevo_request_failed" }, 502);

  // 동의 근거(서버 시각)는 본인 user_metadata 에도 남긴다 — 기존 값은 보존.
  try {
    await supabase.auth.admin.updateUserById(user.id, {
      user_metadata: {
        ...meta,
        newsletter_prompted: true,
        newsletter_consent: { source, ver, at, list: listTag, attrs },
      },
    });
  } catch (_) {
    // 근거 기록 실패가 구독 자체를 되돌리지는 않는다(Brevo 속성이 1차 근거).
  }
  return jsonResponse({ ok: true, status: res.status === 201 ? "subscribed" : "already", attrs, list: listTag });
});
