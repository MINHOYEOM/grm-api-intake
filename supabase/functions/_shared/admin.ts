import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

export const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders,
      "Content-Type": "application/json; charset=utf-8",
    },
  });
}

export function optionsResponse(): Response {
  return new Response("ok", { headers: corsHeaders });
}

export function envAny(names: string[]): string {
  for (const name of names) {
    const value = (Deno.env.get(name) || "").trim();
    if (value) return value;
  }
  return "";
}

export function clampNumber(value: string | null, fallback: number, min: number, max: number): number {
  const n = Number(value || fallback);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(Math.max(Math.floor(n), min), max);
}

export async function readJsonBody(req: Request): Promise<Record<string, unknown>> {
  try {
    const body = await req.json();
    return body && typeof body === "object" ? body as Record<string, unknown> : {};
  } catch (_) {
    return {};
  }
}

export type AdminContext = {
  supabase: any;
  user: { id: string; email?: string | null };
  admin: Record<string, unknown>;
};

export async function requireAdmin(req: Request): Promise<AdminContext | { error: Response }> {
  const authHeader = req.headers.get("Authorization") || "";
  const token = authHeader.replace(/^Bearer\s+/i, "").trim();
  if (!token) return { error: jsonResponse({ error: "missing_auth" }, 401) };

  const supabaseUrl = envAny(["SUPABASE_URL"]);
  const serviceRoleKey = envAny(["SUPABASE_SERVICE_ROLE_KEY"]);
  if (!supabaseUrl || !serviceRoleKey) {
    return { error: jsonResponse({ error: "server_not_configured" }, 500) };
  }

  const supabase = createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data: userData, error: userError } = await supabase.auth.getUser(token);
  if (userError || !userData.user) {
    return { error: jsonResponse({ error: "invalid_session" }, 401) };
  }

  const { data: admin, error: adminError } = await supabase
    .from("admin_user")
    .select("user_id,email,role")
    .eq("user_id", userData.user.id)
    .is("revoked_at", null)
    .maybeSingle();

  if (adminError) return { error: jsonResponse({ error: "admin_lookup_failed" }, 500) };
  if (!admin) return { error: jsonResponse({ error: "forbidden" }, 403) };

  return { supabase, user: userData.user, admin };
}

export async function audit(
  ctx: AdminContext,
  action: string,
  details: Record<string, unknown> = {},
  targetType = "",
  targetId = "",
): Promise<void> {
  try {
    await ctx.supabase.from("admin_audit_log").insert({
      actor_user_id: ctx.user.id,
      action,
      target_type: targetType || null,
      target_id: targetId || null,
      details: { actor_email: ctx.user.email || null, ...details },
    });
  } catch (_) {
    // Auditing should never block the operator action.
  }
}
