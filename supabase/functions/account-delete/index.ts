import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { envAny, jsonResponse, optionsResponse } from "../_shared/admin.ts";

// 회원 본인 계정 삭제. 호출자 = 로그인한 회원(본인 JWT 만) — 관리자 함수들과 달리 admin 권한 불요.
// 이메일은 JWT 에서만 읽는다(본문 입력 무시). 응답·로그·기록 어디에도 이메일을 남기지 않는다.
// 순서(전부 멱등 — 실패하면 계정이 남아 있으므로 같은 호출을 그대로 재시도하면 된다):
//   ① 관리자 거부 ② Brevo 연락처 삭제(404=이미 없음=성공) ③ 문의 이메일 비우기 ④ auth 사용자 삭제
// 삭제 후 reaction 등은 FK on delete cascade 가 정리한다(001·015·016·032·067).

const REAUTH_WINDOW_SEC = 300;

type Steps = Record<string, boolean>;

function jwtPayload(token: string): Record<string, unknown> {
  try {
    const part = token.split(".")[1] || "";
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/");
    const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
    return JSON.parse(atob(padded));
  } catch (_) {
    return {};
  }
}

// 최근 인증 시각 = amr 의 가장 최근 timestamp(자동 토큰 갱신은 이 값을 바꾸지 않는다).
function lastAuthAgeSec(payload: Record<string, unknown>, nowSec: number): number {
  const amr = Array.isArray(payload.amr) ? payload.amr as Array<Record<string, unknown>> : [];
  let latest = 0;
  for (const a of amr) {
    const ts = Number(a && a.timestamp);
    if (Number.isFinite(ts) && ts > latest) latest = ts;
  }
  return latest ? nowSec - latest : Number.POSITIVE_INFINITY;
}

async function brevoDelete(email: string): Promise<boolean> {
  const key = envAny(["BREVO_API_KEY", "NEWSLETTER_API_KEY"]);
  if (!key) return false;
  const res = await fetch(`https://api.brevo.com/v3/contacts/${encodeURIComponent(email)}`, {
    method: "DELETE",
    headers: { "Accept": "application/json", "api-key": key },
  });
  return res.ok || res.status === 404;
}

async function logResult(supabase: any, ok: boolean, failedStep: string | null, steps: Steps) {
  try {
    await supabase.from("account_deletion_log").insert({ ok, failed_step: failedStep, steps });
  } catch (_) {
    // 기록 실패가 삭제를 막지 않는다.
  }
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return optionsResponse();
  if (req.method !== "POST") return jsonResponse({ error: "method_not_allowed" }, 405);

  const token = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "").trim();
  if (!token) return jsonResponse({ error: "missing_auth" }, 401);

  const supabaseUrl = envAny(["SUPABASE_URL"]);
  const serviceRoleKey = envAny(["SUPABASE_SERVICE_ROLE_KEY"]);
  if (!supabaseUrl || !serviceRoleKey) return jsonResponse({ error: "server_not_configured" }, 500);

  const supabase = createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data: userData, error: userError } = await supabase.auth.getUser(token);
  if (userError || !userData.user) return jsonResponse({ error: "invalid_session" }, 401);
  const user = userData.user;
  const email = String(user.email || "").trim().toLowerCase();

  const age = lastAuthAgeSec(jwtPayload(token), Math.floor(Date.now() / 1000));
  if (age > REAUTH_WINDOW_SEC) return jsonResponse({ error: "reauth_required" }, 403);

  const steps: Steps = {};

  const { data: admin, error: adminError } = await supabase
    .from("admin_user").select("user_id").eq("user_id", user.id).is("revoked_at", null).maybeSingle();
  if (adminError) return jsonResponse({ error: "lookup_failed" }, 500);
  if (admin) {
    await logResult(supabase, false, "admin_protected", steps);
    return jsonResponse({ error: "admin_protected" }, 403);
  }

  if (email) {
    steps.brevo = await brevoDelete(email).catch(() => false);
    if (!steps.brevo) {
      await logResult(supabase, false, "brevo", steps);
      return jsonResponse({ error: "brevo_failed" }, 502);
    }
    const { error: scrubError } = await supabase.rpc("account_scrub_feedback", { p_email: email });
    steps.feedback = !scrubError;
    if (scrubError) {
      await logResult(supabase, false, "feedback", steps);
      return jsonResponse({ error: "feedback_failed" }, 502);
    }
  }

  const { error: delError } = await supabase.auth.admin.deleteUser(user.id);
  steps.auth = !delError;
  if (delError) {
    await logResult(supabase, false, "auth", steps);
    return jsonResponse({ error: "delete_failed" }, 502);
  }

  await logResult(supabase, true, null, steps);
  return jsonResponse({ ok: true });
});
