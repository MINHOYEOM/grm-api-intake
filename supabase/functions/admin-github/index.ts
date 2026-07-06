import {
  audit,
  envAny,
  jsonResponse,
  optionsResponse,
  readJsonBody,
  requireAdmin,
} from "../_shared/admin.ts";

type WorkflowDef = {
  label: string;
  workflow: string;
  purpose: string;
  schedule: string;
  group: string;
  manual?: boolean;
  inputs?: Record<string, string | boolean>;
  events?: string[];
};

type WorkflowRun = {
  id?: number;
  run_number?: number;
  display_title?: string;
  status?: string;
  conclusion?: string | null;
  head_branch?: string;
  event?: string;
  run_started_at?: string;
  created_at?: string;
  updated_at?: string;
  html_url?: string;
  actor?: { login?: string };
};

type WorkflowJob = {
  id?: number;
  name?: string;
  status?: string;
  conclusion?: string | null;
  started_at?: string;
  completed_at?: string;
  html_url?: string;
  steps?: Array<{
    name?: string;
    number?: number;
    status?: string;
    conclusion?: string | null;
    started_at?: string;
    completed_at?: string;
  }>;
};

type WorkflowStepSnapshot = {
  name: string;
  number: number | null;
  status: string | null;
  conclusion: string | null;
  started_at: string | null;
  completed_at: string | null;
};

type NormalizedWorkflowJob = {
  id: number | null;
  name: string;
  status: string | null;
  conclusion: string | null;
  started_at: string | null;
  completed_at: string | null;
  html_url: string | null;
  failed_steps: WorkflowStepSnapshot[];
  skipped_steps: WorkflowStepSnapshot[];
};

function repoName(): string {
  return envAny(["GITHUB_REPO", "GITHUB_REPOSITORY"]) || "MINHOYEOM/grm-api-intake";
}

function githubToken(): string {
  return envAny(["GITHUB_ACTIONS_TOKEN", "GITHUB_TOKEN"]);
}

function gitRef(): string {
  return envAny(["GITHUB_REF", "GITHUB_BRANCH"]) || "main";
}

function workflowMap(publishDate = ""): Record<string, WorkflowDef> {
  return {
    intake_run: {
      label: "규제소스 수집",
      workflow: envAny(["INTAKE_WORKFLOW_ID"]) || "grm-intake.yml",
      purpose: "규제기관 소스 수집과 Notion Intake 반영",
      schedule: "매일 03:17 KST 자동 실행",
      group: "source",
      inputs: {},
      events: ["schedule", "workflow_dispatch"],
    },
    brief_audit: {
      label: "브리프 감사",
      workflow: envAny(["BRIEF_AUDIT_WORKFLOW_ID"]) || "grm-brief-audit.yml",
      purpose: "발행 후 provenance와 원문 링크 근거 재검증",
      schedule: "매주 월 11:00 KST 자동 실행",
      group: "quality",
      inputs: {},
      events: ["schedule", "workflow_dispatch"],
    },
    web_deploy: {
      label: "웹 배포",
      workflow: envAny(["WEB_DEPLOY_WORKFLOW_ID"]) || "grm-web-deploy.yml",
      purpose: "정적 사이트 빌드, 링크체크, Cloudflare Pages 배포",
      schedule: "web/** 변경 시 자동 실행",
      group: "publish",
      inputs: {},
      events: ["push", "pull_request", "workflow_dispatch"],
    },
    web_publish: {
      label: "웹 발행",
      workflow: envAny(["WEB_PUBLISH_WORKFLOW_ID"]) || "grm-web-publish.yml",
      purpose: "라우틴 델타 → 발행본 자동 조립 + PR (프리뷰 확인 후 사람이 머지 = 라이브)",
      schedule: "델타 커밋 시 자동 / Admin 수동 실행",
      group: "publish",
      inputs: { publish_date: publishDate },
      events: ["push", "workflow_dispatch"],
    },
    newsletter_send: {
      label: "뉴스레터 실발송",
      workflow: envAny(["NEWSLETTER_WORKFLOW_ID"]) || "grm-newsletter-send.yml",
      purpose: "최신 Weekly Brief를 Brevo 구독자 리스트에 실발송",
      schedule: "Admin 수동 승인 실행",
      group: "newsletter",
      inputs: { publish_date: publishDate, mode: "send" },
      events: ["schedule", "workflow_dispatch"],
    },
    ci: {
      label: "CI 테스트",
      workflow: "grm-ci.yml",
      purpose: "컴파일, 단위 테스트, 렌더 회귀 검증",
      schedule: "push / pull_request 자동 실행",
      group: "quality",
      manual: false,
      events: ["push", "pull_request"],
    },
    admin_backend: {
      label: "Admin 백엔드 배포",
      workflow: "grm-admin-backend-deploy.yml",
      purpose: "Supabase migration과 Admin Edge Function 배포",
      schedule: "Admin 백엔드 변경 시 자동 실행",
      group: "admin",
      inputs: {},
      events: ["push", "workflow_dispatch"],
    },
    keepalive: {
      label: "Supabase Keepalive",
      workflow: "grm-supabase-keepalive.yml",
      purpose: "Supabase 프로젝트 휴면 방지",
      schedule: "정기 자동 실행",
      group: "infra",
      manual: false,
      events: ["schedule", "workflow_dispatch"],
    },
  };
}

async function githubFetch(path: string, init: RequestInit = {}) {
  const token = githubToken();
  if (!token) return { ok: false, status: 500, payload: { error: "github_not_configured" } };
  const res = await fetch(`https://api.github.com/repos/${repoName()}${path}`, {
    ...init,
    headers: {
      "Accept": "application/vnd.github+json",
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
      "User-Agent": "grm-admin-console",
      "X-GitHub-Api-Version": "2022-11-28",
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

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function runIsRecent(run: WorkflowRun, startedAt: string): boolean {
  if (!run.created_at) return false;
  const runTime = Date.parse(run.created_at);
  const started = Date.parse(startedAt) - 10000;
  return Number.isFinite(runTime) && Number.isFinite(started) && runTime >= started;
}

async function findLatestWorkflowRun(workflow: string, startedAt: string): Promise<WorkflowRun | null> {
  for (let i = 0; i < 5; i += 1) {
    await sleep(i === 0 ? 700 : 1400);
    const path = `/actions/workflows/${encodeURIComponent(workflow)}/runs?branch=${encodeURIComponent(gitRef())}&event=workflow_dispatch&per_page=10`;
    const res = await githubFetch(path);
    if (!res.ok) continue;
    const runs = (res.payload as Record<string, unknown>).workflow_runs as WorkflowRun[] | undefined;
    const recent = (runs || []).find((run) => runIsRecent(run, startedAt));
    if (recent) return recent;
  }
  return null;
}

async function listRuns() {
  const defs = workflowMap();
  const out: Array<Record<string, unknown>> = [];
  for (const [action, def] of Object.entries(defs)) {
    const path = `/actions/workflows/${encodeURIComponent(def.workflow)}/runs?per_page=6`;
    const res = await githubFetch(path);
    if (!res.ok) continue;
    const runs = (res.payload as Record<string, unknown>).workflow_runs as Array<Record<string, unknown>> | undefined;
    for (const run of runs || []) {
      const relevant = runMatchesWorkflowDef(def, run);
      out.push({
        action,
        group: def.group,
        workflow_id: def.workflow,
        workflow_name: def.label,
        purpose: def.purpose,
        schedule: def.schedule,
        relevant,
        id: run.id,
        run_number: run.run_number,
        display_title: run.display_title,
        status: run.status,
        conclusion: run.conclusion,
        head_branch: run.head_branch,
        event: run.event,
        run_started_at: run.run_started_at,
        created_at: run.created_at,
        updated_at: run.updated_at,
        html_url: run.html_url,
        actor: (run.actor as Record<string, unknown> | undefined)?.login || null,
      });
    }
  }
  out.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  return out;
}

function runKind(run: Record<string, unknown> | WorkflowRun | null | undefined): "ok" | "warn" | "bad" {
  if (!run) return "warn";
  if (run.status && run.status !== "completed") return "warn";
  if (run.conclusion === "success") return "ok";
  if (["cancelled", "skipped", "neutral"].includes(String(run.conclusion || ""))) return "warn";
  return "bad";
}

function runMatchesWorkflowDef(def: WorkflowDef, run: WorkflowRun | Record<string, unknown>): boolean {
  const event = String(run.event || "");
  return !def.events || !event || def.events.includes(event);
}

function stepSnapshot(step: NonNullable<WorkflowJob["steps"]>[number]): WorkflowStepSnapshot {
  return {
    name: step.name || "-",
    number: step.number || null,
    status: step.status || null,
    conclusion: step.conclusion || null,
    started_at: step.started_at || null,
    completed_at: step.completed_at || null,
  };
}

function normalizeJob(job: WorkflowJob): NormalizedWorkflowJob {
  const failedSteps = (job.steps || [])
    .filter((step) => step.conclusion && !["success", "skipped", "neutral"].includes(String(step.conclusion)))
    .map(stepSnapshot);
  const skippedSteps = (job.steps || [])
    .filter((step) => String(step.conclusion || "") === "skipped")
    .map(stepSnapshot);
  return {
    id: job.id || null,
    name: job.name || "-",
    status: job.status || null,
    conclusion: job.conclusion || null,
    started_at: job.started_at || null,
    completed_at: job.completed_at || null,
    html_url: job.html_url || null,
    failed_steps: failedSteps,
    skipped_steps: skippedSteps,
  };
}

async function allJobsForRun(runId: number | string): Promise<NormalizedWorkflowJob[]> {
  const id = String(runId || "").replace(/[^\d]/g, "");
  if (!id) return [];
  const res = await githubFetch(`/actions/runs/${encodeURIComponent(id)}/jobs?per_page=100`);
  if (!res.ok) return [];
  const jobs = (res.payload as Record<string, unknown>).jobs as WorkflowJob[] | undefined;
  return (jobs || []).map(normalizeJob);
}

async function jobsForRun(runId: number | string) {
  return (await allJobsForRun(runId))
    .filter((job) => job.conclusion && !["success", "skipped", "neutral"].includes(String(job.conclusion)));
}

function isAdminDeployCriticalStep(name: string): boolean {
  const n = name.toLowerCase();
  return n.includes("supabase/setup-cli")
    || n.includes("link supabase project")
    || n.includes("push database migrations")
    || n.includes("set edge function secrets")
    || n.includes("deploy admin edge functions");
}

function adminDeployConfigurationWarnings(
  run: Record<string, unknown> | null,
  jobs: NormalizedWorkflowJob[],
) {
  if (!run) return [];
  const skipped = jobs.flatMap((job) => job.skipped_steps.map((step) => ({
    ...step,
    job_name: job.name,
    job_url: job.html_url,
  }))).filter((step) => isAdminDeployCriticalStep(step.name));
  if (!skipped.length) return [];
  const runId = run.id == null ? null : String(run.id);
  return [{
    code: "admin_backend_deploy_skipped",
    severity: "warn",
    title: "Admin Backend Deploy 실제 배포 단계 skip",
    detail: "GitHub Secrets 누락 가능성이 높습니다: SUPABASE_ACCESS_TOKEN · SUPABASE_SERVICE_ROLE_KEY · SUPABASE_DB_PASSWORD · ADMIN_GITHUB_ACTIONS_TOKEN",
    workflow: "grm-admin-backend-deploy.yml",
    run_id: runId,
    run_number: run.run_number || null,
    run_url: run.html_url || null,
    steps: skipped.slice(0, 8),
  }];
}

async function openWarningIssues() {
  const res = await githubFetch("/issues?state=open&labels=automation-warning&per_page=10");
  if (!res.ok) return [];
  const issues = res.payload as Array<Record<string, unknown>>;
  return (issues || []).map((issue) => ({
    number: issue.number || null,
    title: issue.title || "",
    html_url: issue.html_url || null,
    updated_at: issue.updated_at || null,
    ...warningIssueSummary(issue),
  }));
}

function warningIssueSummary(issue: Record<string, unknown>) {
  const body = typeof issue.body === "string" ? issue.body : "";
  const warningLines = body.split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("- ["))
    .slice(0, 4);
  const warningCodes = warningLines
    .map((line) => line.match(/^- \[([^\]]+)\]/)?.[1] || "")
    .filter(Boolean);
  const warnings = warningLines.map((line) =>
    line
      .replace(/^- \[[^\]]+\]\s*/, "")
      .replace(/\*\*/g, "")
      .replace(/\s+/g, " ")
      .trim()
  );
  const detailBase = warnings.slice(0, 2).map((line) =>
    line.length > 180 ? `${line.slice(0, 177)}...` : line
  ).join(" / ");
  const detail = detailBase
    ? `${detailBase}${warnings.length > 2 ? ` 외 ${warnings.length - 2}건` : ""}`
    : "";
  return {
    detail,
    warning_codes: warningCodes,
    latest_run_url: body.match(/- 최신 Run:\s*(\S+)/)?.[1] || null,
    latest_run_date: body.match(/- 최신 Run date \(KST\):\s*([^\n]+)/)?.[1]?.trim() || null,
  };
}

async function opsOverview() {
  const defs = workflowMap();
  const runs = await listRuns();
  const workflowChecks = await workflowHealth();
  const checkByWorkflow: Record<string, Record<string, unknown>> = {};
  for (const check of workflowChecks) {
    checkByWorkflow[String(check.workflow || "")] = check as Record<string, unknown>;
  }
  const latestByWorkflow: Record<string, Record<string, unknown>> = {};
  for (const run of runs) {
    const workflow = String(run.workflow_id || "");
    if (run.relevant === false) continue;
    if (workflow && !latestByWorkflow[workflow]) latestByWorkflow[workflow] = run;
  }

  const configurationWarnings = [];
  const workflows = [];
  for (const [action, def] of Object.entries(defs)) {
    const latest = latestByWorkflow[def.workflow] || null;
    const check = checkByWorkflow[def.workflow] || {};
    let kind = check.ok === false ? "bad" : runKind(latest);
    let warnings: Array<Record<string, unknown>> = [];
    if (action === "admin_backend" && latest && latest.id) {
      const jobs = await allJobsForRun(String(latest.id));
      warnings = adminDeployConfigurationWarnings(latest, jobs);
      if (warnings.length && kind === "ok") kind = "warn";
      configurationWarnings.push(...warnings);
    }
    workflows.push({
      action,
      label: def.label,
      workflow: def.workflow,
      workflow_url: check.html_url || null,
      workflow_state: check.state || null,
      purpose: def.purpose,
      schedule: def.schedule,
      group: def.group,
      manual: def.manual !== false,
      ok: check.ok !== false && kind !== "bad" && warnings.length === 0,
      kind,
      latest,
      warnings,
    });
  }

  const incidents = workflows
    .map((workflow) => workflow.latest as Record<string, unknown> | null)
    .filter((run): run is Record<string, unknown> => !!run && runKind(run) === "bad")
    .slice(0, 6);
  for (const incident of incidents.slice(0, 4)) {
    incident.failed_jobs = incident.id ? await jobsForRun(String(incident.id)) : [];
  }

  const inProgress = runs.filter((run) => run.status && run.status !== "completed").length;
  const warningIssues = await openWarningIssues();
  const sourceWorkflow = workflows.find((item) => item.action === "intake_run");
  return {
    ok: workflows.every((item) => item.ok),
    repo: repoName(),
    ref: gitRef(),
    generated_at: new Date().toISOString(),
    summary: {
      workflows: workflows.length,
      in_progress: inProgress,
      incidents: incidents.length,
      warning_issues: warningIssues.length,
      configuration_warnings: configurationWarnings.length,
      warning_total: warningIssues.length + configurationWarnings.length,
      source_ok: sourceWorkflow ? sourceWorkflow.ok : false,
      source_status: sourceWorkflow?.latest
        ? ((sourceWorkflow.latest as Record<string, unknown>).conclusion || (sourceWorkflow.latest as Record<string, unknown>).status || "-")
        : "no-run",
    },
    workflows,
    incidents,
    configuration_warnings: configurationWarnings,
    warning_issues: warningIssues,
    workflow_checks: workflowChecks,
    runs,
  };
}

async function workflowHealth() {
  const defs = workflowMap();
  const checks = [];
  for (const [action, def] of Object.entries(defs)) {
    const res = await githubFetch(`/actions/workflows/${encodeURIComponent(def.workflow)}`);
    const payload = res.payload as Record<string, unknown>;
    checks.push({
      action,
      label: def.label,
      workflow: def.workflow,
      ok: res.ok,
      status: res.status,
      state: payload.state || null,
      html_url: payload.html_url || null,
    });
  }
  return checks;
}

async function previousSuccessfulNewsletterDispatch(ctx: Awaited<ReturnType<typeof requireAdmin>>, publishDate: string) {
  if ("error" in ctx) return null;
  const { data, error } = await ctx.supabase
    .from("newsletter_dispatch_log")
    .select("id,publish_date,mode,github_status,github_run_url,github_run_id,created_at")
    .eq("publish_date", publishDate)
    .eq("mode", "send")
    .gte("github_status", 200)
    .lt("github_status", 300)
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) return null;
  return data;
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return optionsResponse();
  const ctx = await requireAdmin(req);
  if ("error" in ctx) return ctx.error;

  const url = new URL(req.url);
  if (req.method === "GET") {
    const action = url.searchParams.get("action") || "runs";
    if (action === "health") {
      if (!githubToken()) {
        return jsonResponse({
          ok: false,
          error: "github_not_configured",
          repo: repoName(),
          ref: gitRef(),
          configured: false,
          workflows: [],
        }, 500);
      }
      const workflows = await workflowHealth();
      return jsonResponse({
        ok: workflows.every((item) => item.ok),
        repo: repoName(),
        ref: gitRef(),
        configured: true,
        workflows,
      });
    }
    if (action === "ops") {
      if (!githubToken()) return jsonResponse({ error: "github_not_configured", runs: [] }, 500);
      return jsonResponse(await opsOverview());
    }
    if (action !== "runs") return jsonResponse({ error: "unknown_action" }, 400);
    if (!githubToken()) return jsonResponse({ error: "github_not_configured", runs: [] }, 500);
    return jsonResponse({ ok: true, repo: repoName(), ref: gitRef(), runs: await listRuns() });
  }

  if (req.method !== "POST") return jsonResponse({ error: "method_not_allowed" }, 405);

  const body = await readJsonBody(req);
  const action = String(body.action || "").trim();
  const publishDate = String(body.publish_date || "").trim();
  const force = body.force === true || String(body.force || "").toLowerCase() === "true";
  if (action === "rerun_failed") {
    const runId = String(body.run_id || "").replace(/[^\d]/g, "");
    if (!runId) return jsonResponse({ error: "missing_run_id" }, 400);
    if (!githubToken()) return jsonResponse({ error: "github_not_configured" }, 500);
    const rerun = await githubFetch(`/actions/runs/${encodeURIComponent(runId)}/rerun-failed-jobs`, {
      method: "POST",
    });
    await audit(ctx, "github.rerun_failed", {
      run_id: runId,
      status: rerun.status,
      response: rerun.payload,
    }, "github.run", runId);
    if (!rerun.ok) {
      return jsonResponse({
        error: "github_rerun_failed",
        status: rerun.status,
        details: rerun.payload,
      }, 502);
    }
    return jsonResponse({
      ok: true,
      action,
      run_id: runId,
      github_status: rerun.status,
      github_run_url: `https://github.com/${repoName()}/actions/runs/${runId}`,
    });
  }
  const defs = workflowMap(publishDate);
  const def = defs[action];
  if (!def) return jsonResponse({ error: "unknown_action" }, 400);
  if (def.manual === false) return jsonResponse({ error: "workflow_not_dispatchable" }, 400);
  if ((action === "newsletter_send" || action === "web_publish") && !/^\d{4}-\d{2}-\d{2}$/.test(publishDate)) {
    return jsonResponse({ error: "invalid_publish_date" }, 400);
  }
  if (!githubToken()) return jsonResponse({ error: "github_not_configured" }, 500);

  if (action === "newsletter_send" && !force) {
    const previous = await previousSuccessfulNewsletterDispatch(ctx, publishDate);
    if (previous) {
      return jsonResponse({
        error: "newsletter_already_dispatched",
        existing: previous,
      }, 409);
    }
  }

  const startedAt = new Date().toISOString();
  const dispatch = await githubFetch(`/actions/workflows/${encodeURIComponent(def.workflow)}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: gitRef(), inputs: def.inputs || {} }),
  });
  const run = dispatch.ok ? await findLatestWorkflowRun(def.workflow, startedAt) : null;
  const workflowUrl = run?.html_url || `https://github.com/${repoName()}/actions/workflows/${def.workflow}`;

  await audit(ctx, "github.dispatch", {
    action,
    workflow: def.workflow,
    ref: gitRef(),
    status: dispatch.status,
    run_id: run?.id || null,
    run_url: run?.html_url || null,
    response: dispatch.payload,
  }, "github.workflow", def.workflow);

  if (action === "newsletter_send") {
    await ctx.supabase.from("newsletter_dispatch_log").insert({
      actor_user_id: ctx.user.id,
      publish_date: publishDate,
      mode: "send",
      workflow: def.workflow,
      ref: gitRef(),
      github_status: dispatch.status,
      github_run_url: workflowUrl,
      github_run_id: run?.id || null,
      github_run_status: run?.status || null,
      github_run_conclusion: run?.conclusion || null,
      github_response: dispatch.payload,
    });
  }

  if (!dispatch.ok) {
    return jsonResponse({
      error: "github_dispatch_failed",
      status: dispatch.status,
      details: dispatch.payload,
    }, 502);
  }

  return jsonResponse({
    ok: true,
    action,
    label: def.label,
    workflow: def.workflow,
    repo: repoName(),
    ref: gitRef(),
    github_status: dispatch.status,
    github_run_id: run?.id || null,
    github_run_status: run?.status || null,
    github_run_conclusion: run?.conclusion || null,
    github_run_url: workflowUrl,
  });
});
