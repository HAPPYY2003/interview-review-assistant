import {
  buildMarkdownReport,
  modeLabel,
  normalizeInterviewRecord,
  normalizeQuestionRecord,
  SCORE_LABELS
} from "./data-model.js";

const app = document.querySelector("#app");
const STORAGE_KEY = "offer-radar-agent-v1";
const QUESTION_TYPES = ["项目经历", "技术知识", "行为面试", "业务理解", "职业规划", "反问环节", "其他"];
const EVENT_TYPES = ["RUN_CREATED", "PHASE_STARTED", "TOOL_FINISHED", "AGENT_STARTED", "AGENT_FINISHED", "EVIDENCE_VALIDATED", "RUN_RESUMED", "RUN_FINISHED", "RUN_FAILED"];
const STATUS_LABELS = {
  draft: "草稿",
  parsing: "解析中",
  waiting_confirmation: "待校对",
  reviewing: "复盘中",
  auditing: "审计中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消"
};
const PHASE_LABELS = {
  queued: "任务排队",
  evidence_review: "证据诊断",
  reflection_audit: "反思审计",
  growth_plan: "成长计划",
  completed: "复盘完成",
  failed: "执行失败",
  resuming: "恢复执行"
};
const AGENT_LABELS = {
  EvidenceAnalyst: "ReAct 证据分析师",
  QualityAuditor: "Reflection 质量审计员",
  GrowthPlanner: "PlanSolve 成长教练"
};

const state = {
  route: parseRoute(),
  records: loadRecords(),
  selectedQuestionId: null,
  events: [],
  health: null,
  trends: [],
  loading: false,
  error: "",
  expandedQuestionId: null
};
let activeSource = null;
let toastTimer = null;

window.addEventListener("hashchange", async () => {
  closeEventSource();
  state.route = parseRoute();
  state.error = "";
  state.expandedQuestionId = null;
  await loadRouteData();
  render();
});

window.addEventListener("unhandledrejection", event => {
  console.error(event.reason);
  setError(readableError(event.reason));
});

init();

async function init() {
  try {
    state.health = await api("/api/health");
    await loadRouteData();
  } catch (error) {
    state.error = readableError(error);
  }
  render();
}

async function loadRouteData() {
  const record = currentRecord();
  if (state.route.name === "run" && record?.runId) {
    try {
      const run = await api(`/api/v1/runs/${record.runId}`);
      state.events = run.events || [];
      record.status = String(run.status || record.status).toLowerCase();
      record.phase = run.phase;
      saveRecord(record);
      if (run.status === "COMPLETED" && !record.report) await loadReport(record);
    } catch (error) {
      state.error = readableError(error);
    }
  }
  if (state.route.name === "trends") {
    try {
      const result = await api("/api/v1/profile/trends");
      state.trends = result.snapshots || [];
    } catch (error) {
      state.error = readableError(error);
    }
  }
  if (!state.selectedQuestionId && record?.questions?.length) state.selectedQuestionId = record.questions[0].id;
}

function parseRoute() {
  const parts = (location.hash.replace(/^#/, "") || "/").split("/").filter(Boolean);
  if (!parts.length) return { name: "home", params: {} };
  if (parts[0] === "new") return { name: "new", params: {} };
  if (parts[0] === "trends") return { name: "trends", params: {} };
  if (parts[0] === "parse" && parts[1]) return { name: "parse", params: { id: parts[1] } };
  if (parts[0] === "run" && parts[1]) return { name: "run", params: { id: parts[1], runId: parts[2] || "" } };
  if (parts[0] === "review" && parts[1]) return { name: "review", params: { id: parts[1] } };
  return { name: "home", params: {} };
}

function render() {
  app.innerHTML = `
    <div class="app-shell">
      ${renderSidebar()}
      <main class="main">${state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : ""}${renderRoute()}</main>
    </div>
    <div class="toast-host" id="toastHost"></div>`;
  bindCommonEvents();
  bindRouteEvents();
  if (state.route.name === "run") connectEventSource();
}

function renderSidebar() {
  const fixture = state.health?.runtime === "fixture";
  return `
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">OR</div>
        <div><p class="brand-title">Offer Radar</p><div class="brand-subtitle">Agent 面试复盘工作台</div></div>
      </div>
      <nav class="nav" aria-label="主导航">
        ${navButton("home", "#/", "◎", "面试记录")}
        ${navButton("new", "#/new", "＋", "新建复盘")}
        ${navButton("trends", "#/trends", "↗", "成长趋势")}
      </nav>
      <div class="runtime-panel">
        <span class="runtime-dot ${fixture ? "fixture" : "live"}"></span>
        <div><strong>${fixture ? "稳定演示模式" : "HelloAgents 实时模式"}</strong><span>${fixture ? "不调用真实模型" : escapeHtml(state.health?.runtime || "连接中")}</span></div>
      </div>
      <div class="sidebar-footer">材料保存在本机 SQLite；模型密钥只从服务端环境变量读取，不进入浏览器。</div>
    </aside>`;
}

function navButton(route, href, icon, label) {
  return `<button class="nav-button ${state.route.name === route ? "active" : ""}" data-nav="${href}"><span aria-hidden="true">${icon}</span><span>${label}</span></button>`;
}

function renderRoute() {
  if (state.route.name === "new") return renderNewPage();
  if (state.route.name === "parse") return renderParsePage();
  if (state.route.name === "run") return renderRunPage();
  if (state.route.name === "review") return renderReviewPage();
  if (state.route.name === "trends") return renderTrendsPage();
  return renderHomePage();
}

function renderHomePage() {
  const records = [...state.records].sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt));
  return `
    <section class="page">
      <div class="page-header">
        <div><span class="eyebrow">INTERVIEW WORKSPACE</span><h1 class="page-title">面试复盘记录</h1><p class="page-desc">从原始回答出发，查看证据、评分、审计和下一步行动。</p></div>
        <button class="button" data-nav="#/new">新建复盘</button>
      </div>
      <div class="surface">
        ${records.length ? `<div class="table">
          <div class="table-row table-head"><div>面试</div><div>轮次</div><div>问题</div><div>状态</div><div></div></div>
          ${records.map(renderRecordRow).join("")}
        </div>` : `<div class="empty"><div class="empty-title">还没有面试记录</div><p>创建第一场复盘，系统会先拆题并等待你确认。</p><button class="button" data-nav="#/new">开始创建</button></div>`}
      </div>
    </section>`;
}

function renderRecordRow(record) {
  const route = record.status === "completed" ? `#/review/${record.id}` : record.runId ? `#/run/${record.id}/${record.runId}` : `#/parse/${record.id}`;
  return `<div class="table-row">
    <div class="table-main"><strong class="table-title">${escapeHtml(record.company || "未填写公司")} · ${escapeHtml(record.position || "未填写岗位")}</strong><span class="table-subtitle">${escapeHtml(record.interviewDate || "未填写日期")}</span></div>
    <div>${escapeHtml(record.round || "--")}</div><div>${record.questions?.length || record.questionCount || 0} 道</div><div>${renderStatus(record.status)}</div>
    <div class="table-actions"><button class="button secondary" data-nav="${route}">${record.status === "completed" ? "查看报告" : "继续"}</button><button class="icon-button" title="删除本地记录" data-delete="${record.id}">×</button></div>
  </div>`;
}

function renderNewPage() {
  return `
    <section class="page">
      ${renderSteps(1)}
      <div class="page-header"><div><span class="eyebrow">NEW REVIEW</span><h1 class="page-title">新建面试复盘</h1><p class="page-desc">支持粘贴文本；JD 和简历也可以上传 PDF/DOCX，面试稿支持 TXT。</p></div><button class="button secondary" id="fillDemo">填入演示数据</button></div>
      <form id="newForm" class="surface section">
        <div class="grid-2">
          ${field("company", "公司名称", "例如：星河科技", true)}
          ${field("position", "岗位名称", "例如：产品经理", true)}
          ${field("round", "面试轮次", "例如：业务二面")}
          ${field("interviewDate", "面试日期", "", false, "date")}
          <label class="field full-span"><span>本次复盘目标</span><input class="input" name="reviewGoal" placeholder="例如：重点检查数据证据和跨团队推动能力" /></label>
        </div>
        <div class="material-grid">
          ${materialInput("jobDescription", "jdFile", "岗位 JD", "粘贴岗位职责、任职要求和加分项...", ".txt,.pdf,.docx")}
          ${materialInput("resumeText", "resumeFile", "简历材料", "粘贴与岗位相关的经历和项目证据...", ".txt,.pdf,.docx")}
        </div>
        ${materialInput("rawTranscript", "transcriptFile", "面试文字稿", "建议保留“面试官：”“候选人：”说话人标记...", ".txt", true)}
        <label class="privacy-check"><input type="checkbox" required /> <span>我确认材料已做必要脱敏，并同意在本机进行分析。</span></label>
        <div class="actions"><button type="button" class="button secondary" data-nav="#/">取消</button><button class="button" type="submit" ${state.loading ? "disabled" : ""}>${state.loading ? "正在解析..." : "解析文字稿"}</button></div>
      </form>
    </section>`;
}

function field(name, label, placeholder, required = false, type = "text") {
  return `<label class="field"><span>${label}</span><input class="input" type="${type}" name="${name}" placeholder="${placeholder}" ${required ? "required" : ""} /></label>`;
}

function materialInput(name, fileName, label, placeholder, accept, large = false) {
  const inputId = `file-${fileName}`;
  const formats = accept.split(",").map(item => item.replace(".", "").toUpperCase()).join(" / ");
  return `<div class="upload-field ${large ? "full-span" : ""}">
    <label class="field"><span>${label}</span><textarea class="textarea ${large ? "transcript-large" : ""}" name="${name}" placeholder="${placeholder}"></textarea></label>
    <div class="file-row">
      <div class="file-copy"><strong>上传 ${formats}</strong><span data-file-name="${fileName}">未选择文件</span></div>
      <input class="file-input" id="${inputId}" type="file" name="${fileName}" accept="${accept}" />
      <label class="file-button" for="${inputId}" title="选择${label}文件"><span aria-hidden="true">↑</span><span data-file-action="${fileName}">选择文件</span></label>
    </div>
  </div>`;
}

function renderParsePage() {
  const record = currentRecord();
  if (!record) return renderMissing();
  const selected = record.questions?.find(item => item.id === state.selectedQuestionId) || record.questions?.[0];
  return `
    <section class="page">
      ${renderSteps(2)}
      <div class="page-header"><div><span class="eyebrow">HUMAN CHECKPOINT</span><h1 class="page-title">${escapeHtml(record.company)} · ${escapeHtml(record.position)}</h1><p class="page-desc">AI 已拆题。请检查问题和回答边界，确认后才会正式评分。</p></div><button class="button" id="startRun" ${state.loading ? "disabled" : ""}>${state.loading ? "正在创建任务..." : "确认题卡并启动 Agent"}</button></div>
      <div class="review-stats"><span><strong>${record.questions?.length || 0}</strong> 道问题</span><span><strong>${(record.questions || []).filter(item => item.confidence === "low").length}</strong> 道低置信度</span><label class="web-toggle"><input id="webVerify" type="checkbox" ${state.health?.webVerifyAvailable ? "" : "disabled"} /> 必要时联网核验</label></div>
      <div class="parse-layout">
        <aside class="surface question-list">${(record.questions || []).map(item => `<button class="question-nav-item ${selected?.id === item.id ? "active" : ""}" data-question="${item.id}"><div class="question-nav-title">${String(item.order).padStart(2, "0")} ${escapeHtml(item.interviewerQuestion)}</div><div class="meta-row"><span class="tag">${escapeHtml(item.questionType)}</span><span class="tag ${item.confidence}">${confidenceLabel(item.confidence)}</span></div></button>`).join("")}</aside>
        <div class="surface">${selected ? renderQuestionEditor(selected) : `<div class="empty">没有识别到问题</div>`}</div>
      </div>
    </section>`;
}

function renderQuestionEditor(question) {
  return `<form id="questionEditor" data-id="${question.id}" class="section">
    <div class="section-title">人工校对题卡</div>
    <label class="field"><span>面试官问题</span><textarea class="textarea editor-question" name="interviewerQuestion" required>${escapeHtml(question.interviewerQuestion)}</textarea></label>
    <label class="field"><span>我的原回答</span><textarea class="textarea transcript-content" name="candidateAnswer">${escapeHtml(question.candidateAnswer)}</textarea></label>
    <label class="field"><span>题型</span><select class="select" name="questionType">${QUESTION_TYPES.map(type => `<option ${type === question.questionType ? "selected" : ""}>${type}</option>`).join("")}</select></label>
    <div class="actions"><button class="button secondary" type="submit">保存修改</button></div>
  </form>`;
}

function renderRunPage() {
  const record = currentRecord();
  if (!record) return renderMissing();
  const phase = record.phase || "queued";
  const terminal = ["completed", "failed", "cancelled"].includes(record.status);
  return `
    <section class="page">
      ${renderSteps(3)}
      <div class="page-header"><div><span class="eyebrow">AGENT WORKFLOW</span><h1 class="page-title">多 Agent 正在复盘</h1><p class="page-desc">${escapeHtml(record.company)} · ${escapeHtml(record.position)} · ${escapeHtml(PHASE_LABELS[phase] || phase)}</p></div>${record.status === "failed" ? `<button class="button" id="resumeRun">恢复任务</button>` : terminal && record.status === "completed" ? `<button class="button" id="openReport">查看报告</button>` : `<span class="live-indicator"><span></span>实时执行</span>`}</div>
      <div class="agent-stage-grid">
        ${agentStage("evidence_review", "ReAct", "证据诊断", phase)}
        ${agentStage("reflection_audit", "Reflection", "质量审计", phase)}
        ${agentStage("growth_plan", "PlanSolve", "成长计划", phase)}
      </div>
      <section class="surface agent-timeline">
        <div class="section-title">执行轨迹</div>
        ${state.events.length ? state.events.map(renderEvent).join("") : `<div class="empty"><div class="empty-title">等待任务事件</div><p>连接建立后会显示阶段和工具状态，不展示模型隐藏思考。</p></div>`}
      </section>
    </section>`;
}

function agentStage(key, agent, title, phase) {
  const order = ["queued", "evidence_review", "reflection_audit", "growth_plan", "completed"];
  const current = order.indexOf(phase);
  const target = order.indexOf(key);
  const status = current > target ? "done" : current === target ? "active" : "pending";
  return `<div class="agent-stage ${status}"><span class="agent-kicker">${agent}</span><strong>${title}</strong><span>${status === "done" ? "已完成" : status === "active" ? "执行中" : "等待中"}</span></div>`;
}

function renderEvent(event) {
  const data = event.data || {};
  const title = data.agent ? AGENT_LABELS[data.agent] || data.agent : data.tool || event.type.replaceAll("_", " ");
  const detail = data.message || (data.hits != null ? `检索并校验 ${data.hits} 条证据` : data.status || "阶段事件已记录");
  return `<div class="timeline-row"><span class="timeline-index">${String(event.id).padStart(2, "0")}</span><div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(detail)}</p></div><time>${formatTime(event.createdAt)}</time></div>`;
}

function renderReviewPage() {
  const record = currentRecord();
  const report = record?.report;
  if (!record) return renderMissing();
  if (!report) return `<section class="page"><div class="empty"><div class="empty-title">报告仍在生成</div><button class="button" data-nav="#/run/${record.id}/${record.runId || ""}">查看进度</button></div></section>`;
  const interview = report.interview;
  const questions = report.questions || [];
  const actions = report.actions || [];
  return `
    <section class="page report-page">
      ${renderSteps(4)}
      <div class="page-header"><div><span class="eyebrow">EVIDENCE REVIEW</span><h1 class="page-title">${escapeHtml(interview.company)} · ${escapeHtml(interview.position)}</h1><p class="page-desc">${escapeHtml(interview.round || "未填写轮次")} · ${escapeHtml(modeLabel(interview.analysisMode))}</p></div><div class="filters"><button class="button secondary" data-nav="#/parse/${record.id}">编辑题卡</button><button class="button" id="exportReport">导出 Markdown</button></div></div>
      <div class="summary-band"><div class="summary-heading"><div><span class="eyebrow">整场结论</span><h2>岗位信号与回答质量</h2></div><span class="mode-badge">证据已审计</span></div><p>${escapeHtml(interview.summary || "暂无总结")}</p></div>
      ${renderScoreSection(interview.overallScores)}
      ${renderRiskSection(interview.topRisks || [])}
      <section class="report-band"><div class="band-header"><div><span class="eyebrow">ACTION PLAN</span><h2>七天行动计划</h2></div></div><div class="action-list">${actions.map(action => renderAction(record, action)).join("")}</div></section>
      <section class="report-band"><div class="band-header"><div><span class="eyebrow">QUESTION REVIEW</span><h2>逐题证据复盘</h2></div></div><div class="accordion">${questions.map(renderQuestionReview).join("")}</div></section>
      <section class="report-band audit-band"><div class="band-header"><div><span class="eyebrow">REFLECTION AUDIT</span><h2>质量审计</h2></div></div><ul>${(interview.auditNotes || []).map(note => `<li>${escapeHtml(note)}</li>`).join("")}</ul></section>
      <div class="metadata-line">${escapeHtml(interview.latestAIMetadata?.provider || "--")} · ${escapeHtml(interview.latestAIMetadata?.model || "--")} · ${formatTime(interview.latestAIMetadata?.generatedAt)}</div>
    </section>`;
}

function renderScoreSection(scores = {}) {
  return `<section class="report-band"><div class="band-header"><div><span class="eyebrow">SCORE</span><h2>五维评分</h2></div><div class="overall-score">${Number(scores.overall || 0).toFixed(1)}<span>/10</span></div></div><div class="score-grid">${Object.entries(SCORE_LABELS).filter(([key]) => key !== "overall").map(([key, label]) => `<div class="score-tile"><div><span>${label}</span><strong>${Number(scores[key] || 0).toFixed(1)}</strong></div><div class="score-bar"><span style="width:${Math.min(100, Number(scores[key] || 0) * 10)}%"></span></div></div>`).join("")}</div></section>`;
}

function renderRiskSection(risks) {
  return `<section class="report-band"><div class="band-header"><div><span class="eyebrow">TOP RISKS</span><h2>优先风险</h2></div></div><div class="risk-list">${risks.length ? risks.map((risk, index) => `<div class="risk-row"><span class="risk-rank">${index + 1}</span><div><strong class="risk-title">${escapeHtml(risk.title)}</strong><p>${escapeHtml(risk.reason)}</p></div><span class="tag ${risk.severity}">${risk.severity === "high" ? "高风险" : "需关注"}</span></div>`).join("") : `<p class="small">没有识别到突出风险。</p>`}</div></section>`;
}

function renderAction(record, action) {
  return `<label class="action-item"><input type="checkbox" data-action="${action.id}" ${action.completed ? "checked" : ""} /><span><strong>${escapeHtml(action.title)}</strong><span class="action-description">${escapeHtml(action.description || "")}</span></span><span class="tag ${action.priority}">${action.priority === "high" ? "高优先" : "中优先"}</span></label>`;
}

function renderQuestionReview(question) {
  const expanded = state.expandedQuestionId === question.id;
  return `<div class="accordion-item"><button class="accordion-button" data-expand="${question.id}"><span><strong>${String(question.order).padStart(2, "0")} ${escapeHtml(question.interviewerQuestion)}</strong><span class="meta-row"><span class="tag">${escapeHtml(question.questionType)}</span><span>${Number(question.scores?.overall || 0).toFixed(1)} 分</span></span></span><span>${expanded ? "−" : "+"}</span></button>${expanded ? `<div class="accordion-panel">
    ${reviewBlock("我的原回答", question.candidateAnswer)}
    ${reviewBlock("AI 诊断", question.diagnosis)}
    <div class="review-block"><h4>评分证据</h4><div class="evidence-list">${(question.scoreEvidence || []).map(item => `<div class="evidence-row"><span class="tag">${escapeHtml(SCORE_LABELS[item.dimension] || item.dimension)} ${Number(item.score || 0).toFixed(1)}</span><p>${escapeHtml(item.rationale || "")}</p>${item.quote ? `<blockquote>“${escapeHtml(item.quote)}”</blockquote>` : `<span class="small">本维度没有可直接引用的原文。</span>`}</div>`).join("")}</div></div>
    <div class="review-block"><h4>证据来源</h4><div class="source-list">${(question.evidenceRefs || []).map(renderEvidenceRef).join("")}</div></div>
    ${renderStar(question.starRewrite || {})}
  </div>` : ""}</div>`;
}

function reviewBlock(title, content) {
  return `<div class="review-block"><h4>${title}</h4><p>${escapeHtml(content || "暂无")}</p></div>`;
}

function renderEvidenceRef(item) {
  const labels = { transcript: "原回答", job_description: "岗位 JD", resume: "简历", knowledge: "本地知识库", web: "联网来源" };
  return `<div class="source-item"><div><span class="tag">${labels[item.sourceType] || item.sourceType}</span><strong>${escapeHtml(item.title || item.sourceId || "证据")}</strong></div><p>${escapeHtml(item.quote || "")}</p><span class="source-meta">${item.verified ? "已回查" : "待核验"} · 置信度 ${Math.round(Number(item.confidence || 0) * 100)}% ${item.locator ? `· ${escapeHtml(item.locator)}` : ""}</span></div>`;
}

function renderStar(star) {
  return `<div class="review-block"><h4>STAR 结构化改写</h4><div class="star-grid"><div><span>S 情境</span><p>${escapeHtml(star.situation || "暂无")}</p></div><div><span>T 任务</span><p>${escapeHtml(star.task || "暂无")}</p></div><div><span>A 行动</span><p>${escapeHtml(star.action || "暂无")}</p></div><div><span>R 结果</span><p>${escapeHtml(star.result || "暂无")}</p></div></div><div class="improved-answer"><span>完整优化回答</span><p>${escapeHtml(star.fullAnswer || "暂无")}</p></div></div>`;
}

function renderTrendsPage() {
  return `<section class="page"><div class="page-header"><div><span class="eyebrow">GROWTH MEMORY</span><h1 class="page-title">成长趋势</h1><p class="page-desc">跨多次面试观察五维变化和重复薄弱项。</p></div></div>${state.trends.length ? `<div class="trend-list">${state.trends.map(renderTrend).join("")}</div>` : `<div class="surface empty"><div class="empty-title">还没有成长快照</div><p>完成一次 Agent 复盘后，这里会生成本地趋势。</p><button class="button" data-nav="#/new">新建复盘</button></div>`}</section>`;
}

function renderTrend(item) {
  return `<div class="surface trend-item"><div class="trend-heading"><div><strong>${escapeHtml(item.company || "未填写公司")} · ${escapeHtml(item.position || "未填写岗位")}</strong><span>${escapeHtml(item.interview_date || "")}</span></div><span class="overall-score compact">${Number(item.scores?.overall || 0).toFixed(1)}</span></div><div class="mini-scores">${Object.entries(SCORE_LABELS).filter(([key]) => key !== "overall").map(([key, label]) => `<div><span>${label}</span><strong>${Number(item.scores?.[key] || 0).toFixed(1)}</strong></div>`).join("")}</div><div class="weak-row">重点提升：${(item.weakDimensions || []).map(key => SCORE_LABELS[key] || key).join("、")}</div></div>`;
}

function renderSteps(active) {
  return `<div class="steps">${["材料输入", "人工校对", "Agent 协作", "复盘报告"].map((label, index) => `<div class="step ${index + 1 <= active ? "active" : ""}"><span class="step-index">${index + 1}</span><span>${label}</span></div>`).join("")}</div>`;
}

function renderStatus(status = "draft") {
  const normalized = String(status).toLowerCase();
  const css = normalized === "waiting_confirmation" ? "pending_review" : normalized === "auditing" ? "reviewing" : normalized;
  return `<span class="status status-${css}"><span class="status-dot"></span>${STATUS_LABELS[normalized] || normalized}</span>`;
}

function renderMissing() {
  return `<section class="page"><div class="empty"><div class="empty-title">没有找到这条面试记录</div><button class="button" data-nav="#/">返回列表</button></div></section>`;
}

function bindCommonEvents() {
  document.querySelectorAll("[data-nav]").forEach(button => button.addEventListener("click", () => { location.hash = button.dataset.nav; }));
  document.querySelectorAll("[data-delete]").forEach(button => button.addEventListener("click", () => {
    if (!confirm("只删除浏览器中的入口记录，确定继续吗？")) return;
    state.records = state.records.filter(item => item.id !== button.dataset.delete);
    persistRecords();
    render();
  }));
}

function bindRouteEvents() {
  if (state.route.name === "new") bindNewPage();
  if (state.route.name === "parse") bindParsePage();
  if (state.route.name === "run") bindRunPage();
  if (state.route.name === "review") bindReviewPage();
}

function bindNewPage() {
  document.querySelector("#fillDemo")?.addEventListener("click", fillDemo);
  document.querySelector("#newForm")?.addEventListener("submit", submitNewInterview);
  document.querySelectorAll(".file-input").forEach(input => input.addEventListener("change", () => updateFileState(input)));
}

function fillDemo() {
  const form = document.querySelector("#newForm");
  const button = document.querySelector("#fillDemo");
  if (!form || !button) return;
  if (button.dataset.demoActive === "true") {
    form.reset();
    document.querySelectorAll(".file-input").forEach(updateFileState);
    button.dataset.demoActive = "false";
    button.textContent = "填入演示数据";
    button.classList.remove("danger");
    toast("演示材料已清除");
    return;
  }
  const demo = {
    company: "星河科技",
    position: "产品经理",
    round: "业务二面",
    interviewDate: new Date().toISOString().slice(0, 10),
    reviewGoal: "重点检查数据证据、个人决策和跨团队推动能力。",
    jobDescription: "负责商业化产品从需求分析到上线复盘的完整闭环；通过数据分析定位问题并设计核心指标；推动研发、设计和运营协作；具备结构化表达能力。",
    resumeText: "负责首页推荐策略优化，通过用户分层推动 4 轮实验，点击率提升 12.6%；搭建曝光到转化的指标体系，协同 8 人跨职能团队完成上线。",
    rawTranscript: "面试官：请介绍一个你负责过的最有挑战的项目。\n候选人：我负责首页推荐策略优化。我先拆解曝光、点击和转化漏斗，又访谈用户，之后推动算法和运营做用户分层实验，四轮实验后点击率提升了 12.6%。\n面试官：这个过程中你个人做的最关键决策是什么？\n候选人：我判断不能只改算法参数，而要先区分新老用户。我提出按生命周期分层，并和算法一起定义特征，最后推动研发排期。\n面试官：如果实验结果不显著，你会怎么判断下一步？\n候选人：我会检查样本量、实验周期和分流，再看核心指标与护栏指标，判断扩大样本还是停止实验。"
  };
  Object.entries(demo).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value; });
  button.dataset.demoActive = "true";
  button.textContent = "清除演示数据";
  button.classList.add("danger");
  toast("演示材料已填入");
}

function updateFileState(input) {
  const file = input.files?.[0];
  const name = document.querySelector(`[data-file-name="${input.name}"]`);
  const action = document.querySelector(`[data-file-action="${input.name}"]`);
  const row = input.closest(".file-row");
  if (name) {
    name.textContent = file ? file.name : "未选择文件";
    name.title = file?.name || "";
  }
  if (action) action.textContent = file ? "更换文件" : "选择文件";
  row?.classList.toggle("has-file", Boolean(file));
}

async function submitNewInterview(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  state.loading = true;
  state.error = "";
  render();
  try {
    const transcriptFile = data.get("transcriptFile");
    let transcript = String(data.get("rawTranscript") || "").trim();
    if (transcriptFile?.size) transcript = (await transcriptFile.text()).trim();
    if (!transcript) throw new Error("请粘贴或上传面试文字稿");
    const id = crypto.randomUUID();
    const payload = {
      id,
      company: String(data.get("company") || "").trim(),
      position: String(data.get("position") || "").trim(),
      round: String(data.get("round") || "").trim(),
      interviewDate: String(data.get("interviewDate") || ""),
      reviewGoal: String(data.get("reviewGoal") || "").trim(),
      jobDescription: String(data.get("jobDescription") || "").trim(),
      resumeText: String(data.get("resumeText") || "").trim(),
      rawTranscript: transcript,
      analysisMode: data.get("jobDescription") || data.get("jdFile")?.size ? (data.get("resumeText") || data.get("resumeFile")?.size ? "full_context" : "job_context") : "general"
    };
    await api("/api/v1/interviews", { method: "POST", body: payload });
    await uploadOptional(id, "job_description", data.get("jdFile"));
    await uploadOptional(id, "resume", data.get("resumeFile"));
    const parsed = await api(`/api/v1/interviews/${id}/parse`, { method: "POST" });
    const record = normalizeInterviewRecord({ ...payload, status: "waiting_confirmation", questions: parsed.questions.map(normalizeQuestionRecord), questionCount: parsed.questions.length });
    saveRecord(record);
    state.loading = false;
    location.hash = `#/parse/${id}`;
  } catch (error) {
    state.loading = false;
    state.error = readableError(error);
    render();
  }
}

async function uploadOptional(interviewId, materialType, file) {
  if (!file?.size) return;
  const body = new FormData();
  body.append("material_type", materialType);
  body.append("file", file);
  await api(`/api/v1/interviews/${interviewId}/materials`, { method: "POST", body, rawBody: true });
}

function bindParsePage() {
  document.querySelectorAll("[data-question]").forEach(button => button.addEventListener("click", () => { state.selectedQuestionId = button.dataset.question; render(); }));
  document.querySelector("#questionEditor")?.addEventListener("submit", event => {
    event.preventDefault();
    const record = currentRecord();
    const question = record?.questions?.find(item => item.id === event.currentTarget.dataset.id);
    if (!question) return;
    const data = new FormData(event.currentTarget);
    question.interviewerQuestion = String(data.get("interviewerQuestion") || "").trim();
    question.candidateAnswer = String(data.get("candidateAnswer") || "").trim();
    question.questionType = String(data.get("questionType") || "其他");
    record.updatedAt = new Date().toISOString();
    saveRecord(record);
    toast("题卡修改已保存");
    render();
  });
  document.querySelector("#startRun")?.addEventListener("click", startAgentRun);
}

async function startAgentRun() {
  const record = currentRecord();
  if (!record?.questions?.length) return;
  state.loading = true;
  state.error = "";
  render();
  try {
    await api(`/api/v1/interviews/${record.id}/questions`, { method: "PATCH", body: { questions: record.questions } });
    await api(`/api/v1/interviews/${record.id}/confirm`, { method: "POST" });
    const run = await api(`/api/v1/interviews/${record.id}/review-runs`, { method: "POST", body: { enableWebVerify: Boolean(document.querySelector("#webVerify")?.checked) } });
    record.runId = run.id;
    record.status = "reviewing";
    record.phase = "queued";
    record.updatedAt = new Date().toISOString();
    saveRecord(record);
    state.loading = false;
    location.hash = `#/run/${record.id}/${run.id}`;
  } catch (error) {
    state.loading = false;
    state.error = readableError(error);
    render();
  }
}

function bindRunPage() {
  document.querySelector("#resumeRun")?.addEventListener("click", resumeRun);
  document.querySelector("#openReport")?.addEventListener("click", () => { location.hash = `#/review/${currentRecord()?.id}`; });
}

function connectEventSource() {
  const record = currentRecord();
  if (!record?.runId || activeSource || ["completed", "failed", "cancelled"].includes(record.status)) return;
  activeSource = new EventSource(`/api/v1/runs/${record.runId}/events`);
  EVENT_TYPES.forEach(type => activeSource.addEventListener(type, async event => {
    const item = { id: Number(event.lastEventId || state.events.length + 1), type, data: JSON.parse(event.data || "{}"), createdAt: new Date().toISOString() };
    if (!state.events.some(existing => existing.id === item.id)) state.events.push(item);
    if (type === "PHASE_STARTED") record.phase = item.data.phase;
    if (type === "RUN_FAILED") { record.status = "failed"; record.phase = "failed"; closeEventSource(); }
    if (type === "RUN_FINISHED") {
      record.status = "completed";
      record.phase = "completed";
      closeEventSource();
      await loadReport(record);
      saveRecord(record);
      render();
      setTimeout(() => { location.hash = `#/review/${record.id}`; }, 500);
      return;
    }
    saveRecord(record);
    render();
  }));
  activeSource.onerror = () => {
    if (!["completed", "failed"].includes(record.status)) console.warn("SSE connection interrupted; EventSource will retry.");
  };
}

function closeEventSource() {
  if (activeSource) activeSource.close();
  activeSource = null;
}

async function resumeRun() {
  const record = currentRecord();
  if (!record?.runId) return;
  try {
    await api(`/api/v1/runs/${record.runId}/resume`, { method: "POST" });
    record.status = "reviewing";
    record.phase = "resuming";
    saveRecord(record);
    render();
  } catch (error) {
    setError(readableError(error));
  }
}

async function loadReport(record) {
  const report = await api(`/api/v1/interviews/${record.id}/report`);
  if (report.status !== "COMPLETED") return;
  report.questions = (report.questions || []).map(normalizeQuestionRecord);
  record.report = report;
  record.questions = report.questions;
  record.actions = report.actions || [];
  record.status = "completed";
  record.questionCount = report.questions.length;
  record.updatedAt = new Date().toISOString();
  saveRecord(record);
}

function bindReviewPage() {
  document.querySelectorAll("[data-expand]").forEach(button => button.addEventListener("click", () => { state.expandedQuestionId = state.expandedQuestionId === button.dataset.expand ? null : button.dataset.expand; render(); }));
  document.querySelectorAll("[data-action]").forEach(input => input.addEventListener("change", () => {
    const record = currentRecord();
    const action = record?.report?.actions?.find(item => item.id === input.dataset.action);
    if (action) action.completed = input.checked;
    saveRecord(record);
  }));
  document.querySelector("#exportReport")?.addEventListener("click", exportReport);
}

function exportReport() {
  const record = currentRecord();
  if (!record?.report) return;
  const markdown = buildMarkdownReport(record.report.interview, record.report.questions || [], record.report.actions || []);
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${record.company || "面试"}-${record.position || "复盘"}.md`;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function api(url, options = {}) {
  const init = { method: options.method || "GET", headers: {} };
  if (options.body !== undefined) {
    if (options.rawBody) init.body = options.body;
    else {
      init.headers["content-type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
  }
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `请求失败（${response.status}）`);
  return payload;
}

function loadRecords() {
  try {
    const rows = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(rows) ? rows.map(normalizeInterviewRecord) : [];
  } catch {
    return [];
  }
}

function persistRecords() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state.records));
}

function saveRecord(record) {
  if (!record) return;
  const index = state.records.findIndex(item => item.id === record.id);
  if (index >= 0) state.records[index] = record;
  else state.records.unshift(record);
  persistRecords();
}

function currentRecord() {
  return state.records.find(item => item.id === state.route.params.id);
}

function setError(message) {
  state.error = message;
  render();
}

function toast(message) {
  clearTimeout(toastTimer);
  const host = document.querySelector("#toastHost");
  if (!host) return;
  host.innerHTML = `<div class="toast">${escapeHtml(message)}</div>`;
  toastTimer = setTimeout(() => { if (host) host.innerHTML = ""; }, 2600);
}

function confidenceLabel(value) {
  return { high: "高置信度", medium: "中置信度", low: "低置信度" }[value] || "待确认";
}

function formatTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
}

function readableError(error) {
  return error instanceof Error ? error.message : String(error || "未知错误");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}
