import {
  audit,
  clampNumber,
  envAny,
  jsonResponse,
  optionsResponse,
  readJsonBody,
  requireAdmin,
} from "../_shared/admin.ts";

function apiKey(): string {
  return envAny(["BREVO_API_KEY", "NEWSLETTER_API_KEY"]);
}

function listId(): string {
  return envAny(["BREVO_LIST_ID", "GRM_NEWSLETTER_LIST_ID"]);
}

function brevoHeaders(): HeadersInit {
  return {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "api-key": apiKey(),
  };
}

async function brevo(path: string, init: RequestInit = {}) {
  if (!apiKey()) return { ok: false, status: 500, payload: { error: "brevo_not_configured" } };
  const res = await fetch(`https://api.brevo.com/v3${path}`, {
    ...init,
    headers: {
      ...brevoHeaders(),
      ...(init.headers || {}),
    },
  });
  const text = await res.text();
  let payload: unknown = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (_) {
      payload = { raw: text.slice(0, 1600) };
    }
  }
  return { ok: res.ok, status: res.status, payload };
}

function cleanEmail(value: unknown): string {
  return String(value || "").trim().toLowerCase();
}


// ── 회원 ∩ 구독자 대조(숫자만) ─────────────────────────────────────────────
// 두 명단(auth 회원·Brevo 리스트)을 서버에서만 대조하고 **개수만** 돌려준다 — 이메일은 응답·로그·감사
// 기록 어디에도 남기지 않는다. 버튼을 눌렀을 때만 호출되고(자동 폴링 없음), 결과는 10분간 인스턴스
// 메모리에 캐시해 Brevo API 호출을 아낀다. 상한(페이지 수)을 넘으면 truncated 로 알린다.
const OVERLAP_TTL_MS = 10 * 60 * 1000;
const OVERLAP_MAX_PAGES = 20; // Brevo 500건×20 = 1만 건, 회원 1000건×20 = 2만 명
let overlapCache: { at: number; body: Record<string, unknown> } | null = null;

async function memberOverlap(ctx: { supabase: any }) {
  if (overlapCache && Date.now() - overlapCache.at < OVERLAP_TTL_MS) {
    return { ...overlapCache.body, cached: true };
  }
  let truncated = false;
  const subs = new Set<string>();
  for (let page = 0; page < OVERLAP_MAX_PAGES; page++) {
    const res = await brevo(`/contacts/lists/${encodeURIComponent(listId())}/contacts?limit=500&offset=${page * 500}`);
    if (!res.ok) return { error: "brevo_request_failed", status: res.status };
    const rows = ((res.payload as Record<string, unknown>).contacts || []) as Array<Record<string, unknown>>;
    for (const r of rows) {
      if (r.emailBlacklisted) continue; // 수신거부는 구독자로 세지 않는다
      const e = cleanEmail(r.email);
      if (e) subs.add(e);
    }
    if (rows.length < 500) break;
    if (page === OVERLAP_MAX_PAGES - 1) truncated = true;
  }
  const { data: adminRows } = await ctx.supabase.from("admin_user").select("email").is("revoked_at", null);
  const admins = new Set(((adminRows || []) as Array<Record<string, unknown>>).map((a) => cleanEmail(a.email)));
  let members = 0;
  let overlap = 0;
  for (let page = 1; page <= OVERLAP_MAX_PAGES; page++) {
    const { data, error } = await ctx.supabase.auth.admin.listUsers({ page, perPage: 1000 });
    if (error) return { error: "auth_list_failed" };
    const users = (data?.users || []) as Array<Record<string, unknown>>;
    for (const u of users) {
      const e = cleanEmail(u.email);
      if (!e || admins.has(e)) continue;
      members++;
      if (subs.has(e)) overlap++;
    }
    if (users.length < 1000) break;
    if (page === OVERLAP_MAX_PAGES) truncated = true;
  }
  const body = {
    ok: true,
    members,
    subscribers: subs.size,
    overlap,
    member_only: members - overlap,
    truncated,
    computed_at: new Date().toISOString(),
  };
  overlapCache = { at: Date.now(), body };
  return { ...body, cached: false };
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return optionsResponse();
  const ctx = await requireAdmin(req);
  if ("error" in ctx) return ctx.error;

  const url = new URL(req.url);
  const action = url.searchParams.get("action") || "subscribers";
  const limit = clampNumber(url.searchParams.get("limit"), 50, 1, 500);
  const offset = clampNumber(url.searchParams.get("offset"), 0, 0, 100000);

  if (req.method === "GET") {
    if (!apiKey()) return jsonResponse({ error: "brevo_not_configured" }, 500);
    if (action === "health") {
      if (!listId()) {
        return jsonResponse({
          ok: false,
          configured: true,
          list_configured: false,
          error: "brevo_list_not_configured",
        }, 500);
      }
      const [list, contacts, campaigns] = await Promise.all([
        brevo(`/contacts/lists/${encodeURIComponent(listId())}`),
        brevo(`/contacts/lists/${encodeURIComponent(listId())}/contacts?limit=1&offset=0`),
        brevo(`/emailCampaigns?type=classic&limit=5&offset=0&sort=desc`),
      ]);
      return jsonResponse({
        ok: list.ok && contacts.ok && campaigns.ok,
        configured: true,
        list_configured: true,
        list_id: listId(),
        list: list.payload,
        contacts: contacts.payload,
        campaigns: campaigns.payload,
        statuses: {
          list: list.status,
          contacts: contacts.status,
          campaigns: campaigns.status,
        },
      }, list.ok && contacts.ok && campaigns.ok ? 200 : 502);
    }
    if (action === "subscribers") {
      if (!listId()) return jsonResponse({ error: "brevo_list_not_configured" }, 500);
      const res = await brevo(`/contacts/lists/${encodeURIComponent(listId())}/contacts?limit=${limit}&offset=${offset}`);
      if (!res.ok) return jsonResponse({ error: "brevo_request_failed", status: res.status, details: res.payload }, 502);
      return jsonResponse(res.payload);
    }
    if (action === "member_overlap") {
      if (!listId()) return jsonResponse({ error: "brevo_list_not_configured" }, 500);
      const out = await memberOverlap(ctx);
      if ("error" in out) return jsonResponse(out, 502);
      await audit(ctx, "brevo.member_overlap", { cached: out.cached }); // 숫자 조회 사실만(이메일 없음)
      return jsonResponse(out);
    }
    if (action === "campaigns") {
      const res = await brevo(`/emailCampaigns?type=classic&limit=${Math.min(limit, 100)}&offset=${offset}&sort=desc`);
      if (!res.ok) return jsonResponse({ error: "brevo_request_failed", status: res.status, details: res.payload }, 502);
      return jsonResponse(res.payload);
    }
    if (action === "summary") {
      if (!listId()) return jsonResponse({ error: "brevo_list_not_configured" }, 500);
      const [contacts, campaigns] = await Promise.all([
        brevo(`/contacts/lists/${encodeURIComponent(listId())}/contacts?limit=1&offset=0`),
        brevo(`/emailCampaigns?type=classic&limit=5&offset=0&sort=desc`),
      ]);
      return jsonResponse({ ok: contacts.ok && campaigns.ok, contacts: contacts.payload, campaigns: campaigns.payload });
    }
    return jsonResponse({ error: "unknown_action" }, 400);
  }

  if (req.method !== "POST") return jsonResponse({ error: "method_not_allowed" }, 405);
  if (!apiKey()) return jsonResponse({ error: "brevo_not_configured" }, 500);
  if (!listId()) return jsonResponse({ error: "brevo_list_not_configured" }, 500);

  const body = await readJsonBody(req);
  const postAction = String(body.action || "").trim();
  const email = cleanEmail(body.email);
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return jsonResponse({ error: "invalid_email" }, 400);
  }

  if (postAction === "subscribe") {
    const res = await brevo("/contacts", {
      method: "POST",
      body: JSON.stringify({
        email,
        listIds: [Number(listId())],
        updateEnabled: true,
      }),
    });
    await audit(ctx, "brevo.subscribe", { status: res.status }, "brevo.contact", email);
    if (!res.ok) return jsonResponse({ error: "brevo_request_failed", status: res.status, details: res.payload }, 502);
    return jsonResponse({ ok: true, contact: res.payload });
  }

  if (postAction === "remove_from_list") {
    const res = await brevo(`/contacts/lists/${encodeURIComponent(listId())}/contacts/remove`, {
      method: "POST",
      body: JSON.stringify({ emails: [email] }),
    });
    await audit(ctx, "brevo.remove_from_list", { status: res.status }, "brevo.contact", email);
    if (!res.ok) return jsonResponse({ error: "brevo_request_failed", status: res.status, details: res.payload }, 502);
    return jsonResponse({ ok: true, process: res.payload });
  }

  return jsonResponse({ error: "unknown_action" }, 400);
});
