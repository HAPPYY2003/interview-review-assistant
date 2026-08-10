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
const EVENT_TYPES = [
  "RUN_CREATED", "SUPERVISOR_PLAN_ACCEPTED", "SUPERVISOR_PLAN_FALLBACK", "PHASE_STARTED",
  "TOPIC_ANALYSIS_STARTED", "TOPIC_ANALYSIS_COMPLETED", "SUBMISSION_REJECTED", "CHECKPOINT_SAVED",
  "AUDIT_STARTED", "AUDIT_COMPLETED", "AUDIT_RECOVERY_STARTED", "REVISION_REQUIRED", "TOPIC_REVISION_COMPLETED",
  "GROWTH_PLAN_COMPLETED", "AGENT_STARTED", "AGENT_FINISHED", "RUN_RESUMED", "FALLBACK_REQUESTED",
  "FALLBACK_STARTED", "RUN_FINISHED", "RUN_FAILED"
];
const PARSE_EVENT_TYPES = ["PARSE_CREATED", "PARSE_PHASE_STARTED", "PARSE_TOOL_FINISHED", "AGENT_STARTED", "AGENT_FINISHED", "PARSE_FINISHED", "PARSE_FAILED"];
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
  fallback: "生成降级报告",
  completed: "复盘完成",
  failed: "执行失败",
  resuming: "恢复执行"
};
const PARSE_PHASE_LABELS = {
  queued: "等待解析",
  inspecting: "材料检查",
  transcribing: "云端转写",
  validating: "质量校验",
  structuring: "语义拆题",
  submitting: "结果合并",
  completed: "等待人工校对",
  failed: "解析失败"
};
const AGENT_LABELS = {
  "ReActAgent ParseAgent": "ReAct 材料解析主管",
  EvidenceAnalyst: "ReAct 证据分析师",
  QualityAuditor: "Reflection 质量审计员",
  GrowthPlanner: "PlanSolve 成长教练"
};
const EVENT_LABELS = {
  RUN_CREATED: "复盘任务已创建",
  SUPERVISOR_PLAN_ACCEPTED: "主管计划已验证",
  SUPERVISOR_PLAN_FALLBACK: "主管计划已回退",
  PHASE_STARTED: "阶段开始",
  TOPIC_ANALYSIS_STARTED: "主题分析开始",
  TOPIC_ANALYSIS_COMPLETED: "主题分析完成",
  SUBMISSION_REJECTED: "结构化提交被拒",
  CHECKPOINT_SAVED: "检查点已保存",
  AUDIT_STARTED: "Reflection 审计开始",
  AUDIT_COMPLETED: "Reflection 审计完成",
  AUDIT_RECOVERY_STARTED: "审计恢复开始",
  REVISION_REQUIRED: "主题需要修订",
  TOPIC_REVISION_COMPLETED: "主题修订完成",
  GROWTH_PLAN_COMPLETED: "成长计划完成",
  AGENT_STARTED: "Agent 开始执行",
  AGENT_FINISHED: "Agent 执行结束",
  RUN_RESUMED: "任务已恢复",
  FALLBACK_REQUESTED: "已请求降级报告",
  FALLBACK_STARTED: "降级报告开始生成",
  RUN_FINISHED: "复盘任务完成",
  RUN_FAILED: "复盘任务失败"
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
  expandedQuestionId: null,
  editingTurnId: null,
  expandedFollowUpIds: [],
  collapsedFollowUpIds: [],
  reviewMode: null,
  acknowledgeUnresolved: false,
  acknowledgeUnreviewed: false,
  reviewDialogOpen: false,
  reviewNavFilter: "all"
};
let activeSource = null;
let toastTimer = null;

window.addEventListener("hashchange", async () => {
  closeEventSource();
  state.route = parseRoute();
  state.error = "";
  state.expandedQuestionId = null;
  state.editingTurnId = null;
  state.expandedFollowUpIds = [];
  state.collapsedFollowUpIds = [];
  state.reviewMode = null;
  state.acknowledgeUnresolved = false;
  state.acknowledgeUnreviewed = false;
  state.reviewDialogOpen = false;
  state.reviewNavFilter = "all";
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
  if (state.route.name === "parse" && record?.parseRunId) {
    try {
      const run = await api(`/api/v1/parse-runs/${record.parseRunId}`);
      record.parseEvents = run.events || record.parseEvents || [];
      record.phase = run.phase;
      record.status = run.status === "COMPLETED" ? "waiting_confirmation" : run.status === "FAILED" ? "failed" : "parsing";
      if (run.status === "COMPLETED") await loadParseResult(record);
      if (run.status === "FAILED") record.parseError = run.error || "解析失败";
      saveRecord(record);
    } catch (error) {
      state.error = readableError(error);
    }
  }
  if (state.route.name === "run" && record?.runId) {
    try {
      const run = await api(`/api/v1/runs/${record.runId}`);
      state.events = run.events || [];
      record.status = String(run.status || record.status).toLowerCase();
      record.phase = run.phase;
      record.runProgress = run.progress || record.runProgress || {};
      record.agentMode = run.agent_mode || record.agentMode;
      record.degraded = Boolean(run.degraded);
      record.runError = run.error || "";
      record.failureCode = run.failure_code || "";
      record.agentArtifacts = run.artifacts || [];
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
  if (state.route.name === "parse") connectParseEventSource();
  if (state.reviewDialogOpen) {
    requestAnimationFrame(() => {
      const dialog = document.querySelector("#reviewDialog");
      if (dialog && !dialog.open) dialog.showModal();
    });
  }
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
          <div class="table-row table-head"><div>面试</div><div>日期</div><div>轮次</div><div>问题</div><div>状态</div><div class="table-actions-heading">操作</div></div>
          ${records.map(renderRecordRow).join("")}
        </div>` : `<div class="empty"><div class="empty-title">还没有面试记录</div><p>创建第一场复盘，系统会先拆题并等待你确认。</p><button class="button" data-nav="#/new">开始创建</button></div>`}
      </div>
    </section>`;
}

function renderRecordRow(record) {
  const route = record.status === "completed" ? `#/review/${record.id}` : ["reviewing", "auditing"].includes(record.status) && record.runId ? `#/run/${record.id}/${record.runId}` : `#/parse/${record.id}`;
  const primaryActionLabel = record.status === "completed" ? "查看报告" : "继续复盘";
  const primaryActionIcon = record.status === "completed" ? "file-text" : "arrow-right";
  const interviewDate = record.interviewDate || "未填写日期";
  return `<div class="table-row data">
    <div class="table-main"><button class="table-title record-title-link" type="button" data-nav="${route}" aria-label="${primaryActionLabel}：${escapeHtml(record.company || "未填写公司")} · ${escapeHtml(record.position || "未填写岗位")}">${escapeHtml(record.company || "未填写公司")} · ${escapeHtml(record.position || "未填写岗位")}</button></div>
    <time class="table-meta table-date" data-label="日期" datetime="${escapeHtml(record.interviewDate || "")}">${escapeHtml(interviewDate)}</time>
    <div class="table-meta" data-label="轮次">${escapeHtml(record.round || "--")}</div>
    <div class="table-meta" data-label="问题">${record.questions?.length || record.questionCount || 0} 道</div>
    <div class="table-meta table-status" data-label="状态">${renderStatus(record.status)}</div>
    <div class="table-actions">
      <button class="icon-button record-action" type="button" aria-label="${primaryActionLabel}" data-tooltip="${primaryActionLabel}" data-nav="${route}">${renderLucideIcon(primaryActionIcon)}</button>
      <button class="icon-button record-action danger-icon" type="button" aria-label="删除面试记录" data-tooltip="删除面试记录" data-delete="${record.id}">${renderLucideIcon("trash-2")}</button>
    </div>
  </div>`;
}

function renderLucideIcon(name) {
  const paths = {
    "arrow-right": '<path d="M5 12h14"></path><path d="m12 5 7 7-7 7"></path>',
    "arrow-left": '<path d="m12 19-7-7 7-7"></path><path d="M19 12H5"></path>',
    "check": '<path d="M20 6 9 17l-5-5"></path>',
    "chevron-down": '<path d="m6 9 6 6 6-6"></path>',
    "chevron-up": '<path d="m18 15-6-6-6 6"></path>',
    "circle-check": '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><path d="m9 11 3 3L22 4"></path>',
    "file-text": '<path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="8" x2="16" y1="13" y2="13"></line><line x1="8" x2="16" y1="17" y2="17"></line>',
    "locate-fixed": '<line x1="2" x2="5" y1="12" y2="12"></line><line x1="19" x2="22" y1="12" y2="12"></line><line x1="12" x2="12" y1="2" y2="5"></line><line x1="12" x2="12" y1="19" y2="22"></line><circle cx="12" cy="12" r="7"></circle><circle cx="12" cy="12" r="3"></circle>',
    "git-merge": '<circle cx="18" cy="18" r="3"></circle><circle cx="6" cy="6" r="3"></circle><path d="M6 21V9a9 9 0 0 0 9 9"></path>',
    "pencil": '<path d="M21.17 6.17 17.83 2.83a2.83 2.83 0 0 0-4 0L3 13.66V21h7.34L21.17 10.17a2.83 2.83 0 0 0 0-4Z"></path><path d="m12.5 4.5 7 7"></path>',
    "settings-2": '<path d="M20 7h-9"></path><path d="M14 17H5"></path><circle cx="17" cy="17" r="3"></circle><circle cx="7" cy="7" r="3"></circle>',
    "split": '<circle cx="12" cy="18" r="3"></circle><circle cx="6" cy="6" r="3"></circle><circle cx="18" cy="6" r="3"></circle><path d="M18 9v1a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V9"></path><path d="M12 12v3"></path>',
    "triangle-alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.46 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path>',
    "trash-2": '<path d="M3 6h18"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"></path><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" x2="10" y1="11" y2="17"></line><line x1="14" x2="14" y1="11" y2="17"></line>',
    "x": '<path d="M18 6 6 18"></path><path d="m6 6 12 12"></path>',
  };
  return `<svg class="lucide-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths[name] || ""}</svg>`;
}

function renderNewPage() {
  return `
    <section class="page">
      ${renderSteps(1)}
      <div class="page-header"><div><span class="eyebrow">NEW REVIEW</span><h1 class="page-title">新建面试复盘</h1><p class="page-desc">录入岗位与简历材料，并选择一种面试内容来源。</p></div><button class="button secondary" id="fillDemo">填入演示数据</button></div>
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
        ${renderTranscriptSource()}
        <label class="privacy-check"><input type="checkbox" name="privacyConsent" required /> <span>我确认全部材料已做必要脱敏，并同意在本机保存分析记录。</span></label>
        <div class="actions"><button type="button" class="button secondary" data-nav="#/">取消</button><button class="button" type="submit" ${state.loading ? "disabled" : ""}>${state.loading ? "正在创建解析任务..." : "开始解析"}</button></div>
      </form>
    </section>`;
}

function renderTranscriptSource() {
  const audioDisabled = !state.health?.audioTranscriptionAvailable;
  return `<section class="transcript-source full-span">
    <div class="source-heading"><div><span class="field-label">面试内容</span><p>三种来源互斥，原文会只读保存，人工修改另行记录。</p></div></div>
    <input type="hidden" name="transcriptMode" value="paste" />
    <div class="segmented-control" role="tablist" aria-label="面试内容来源">
      <button type="button" class="segment-button active" data-transcript-mode="paste" aria-selected="true">粘贴文字</button>
      <button type="button" class="segment-button" data-transcript-mode="text" aria-selected="false">上传文字稿</button>
      <button type="button" class="segment-button" data-transcript-mode="audio" aria-selected="false" ${audioDisabled ? "disabled title=\"服务端尚未配置 DEEPGRAM_API_KEY\"" : ""}>上传音频</button>
    </div>
    <div class="source-panel" data-source-panel="paste">
      <label class="field"><span>面试文字稿</span><textarea class="textarea transcript-large" name="rawTranscript" placeholder="建议保留“面试官：”“候选人：”说话人标记；没有标记也可以交给 Agent 判断。"></textarea></label>
    </div>
    <div class="source-panel hidden" data-source-panel="text">
      ${filePicker("transcriptFile", "选择 TXT 文件", ".txt", "仅支持 TXT，文件内容不会在浏览器中改写。")}
    </div>
    <div class="source-panel hidden" data-source-panel="audio">
      ${filePicker("audioFile", "选择音频文件", ".mp3,.m4a,.wav,.flac,.ogg", "支持 MP3、M4A、WAV、FLAC、OGG；最大 200MB、120 分钟。")}
      <label class="cloud-consent"><input type="checkbox" name="cloudConsent" /> <span>我已取得相关人员授权，并同意将音频发送到 Deepgram 云端转写。</span></label>
    </div>
  </section>`;
}

function filePicker(name, title, accept, description) {
  const inputId = `file-${name}`;
  return `<div class="file-row source-file-row">
    <div class="file-copy"><strong>${title}</strong><span>${description}</span><span class="selected-file" data-file-name="${name}">未选择文件</span></div>
    <input class="file-input" id="${inputId}" type="file" name="${name}" accept="${accept}" />
    <label class="file-button" for="${inputId}" title="${title}"><span aria-hidden="true">↑</span><span data-file-action="${name}">选择文件</span></label>
  </div>`;
}

function field(name, label, placeholder, required = false, type = "text") {
  if (type === "date") {
    return `<label class="field"><span>${label}</span><div class="date-control">
      <input class="input date-text-input" type="text" name="${name}" placeholder="YYYY-MM-DD" inputmode="numeric" maxlength="10" pattern="[0-9]{4}-[0-9]{2}-[0-9]{2}" autocomplete="off" ${required ? "required" : ""} />
      <input class="native-date-picker" type="date" data-date-picker="${name}" aria-label="选择${label}" tabindex="-1" />
    </div></label>`;
  }
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
  if (record.status === "parsing" || !record.topics?.length) {
    return renderParseProgress(record);
  }
  const reviewedCount = record.topics.filter(topicIsReviewed).length;
  const priorityTopics = record.topics.filter(topicNeedsPriorityReview);
  if (state.reviewNavFilter === "priority" && !priorityTopics.length) state.reviewNavFilter = "all";
  const visibleTopics = state.reviewNavFilter === "priority" ? priorityTopics : record.topics;
  const selected = visibleTopics.find(item => item.id === state.selectedQuestionId) || visibleTopics[0] || record.topics[0];
  if (selected && state.selectedQuestionId !== selected.id) state.selectedQuestionId = selected.id;
  const unresolvedQuestions = (record.questions || []).filter(item => item.needsConfirmation).length;
  const unresolvedCount = Number(record.unresolvedCount || 0) + unresolvedQuestions;
  const pendingCount = record.topics.length - reviewedCount;
  const allTopicsReviewed = pendingCount === 0;
  const progress = record.topics.length ? Math.round(reviewedCount / record.topics.length * 100) : 0;
  return `
    <section class="page review-page">
      ${renderSteps(2)}
      <div class="review-header">
        <div class="review-header-copy"><h1 class="page-title">${escapeHtml(record.company)} · ${escapeHtml(record.position)}</h1><p class="page-desc">核对主题、主问题和追问，原始材料可随时回查。</p></div>
        <div class="review-header-status" aria-label="校对进度">
          <div class="review-header-progress"><span>校对进度</span><strong>${reviewedCount}/${record.topics.length} 个主题已确认</strong><div class="review-progress-track"><span style="width:${progress}%"></span></div></div>
          <div class="review-header-buttons">
            <button type="button" class="button compact" id="openReviewDialog">开始 Agent 复盘</button>
          </div>
        </div>
      </div>
      <div class="parse-layout review-layout">
        <aside class="surface question-list">
          <div class="question-list-filter" role="group" aria-label="题卡筛选">
            <button type="button" class="${state.reviewNavFilter === "all" ? "active" : ""}" data-review-filter="all" aria-pressed="${state.reviewNavFilter === "all"}">全部题卡</button>
            <button type="button" class="${state.reviewNavFilter === "priority" ? "active" : ""}" data-review-filter="priority" aria-pressed="${state.reviewNavFilter === "priority"}" ${priorityTopics.length ? "" : "disabled"}>需重点校对 ${priorityTopics.length}</button>
          </div>
          <div class="question-list-items">${visibleTopics.map(item => renderTopicNavigationItem(item, record.topics.indexOf(item), selected?.id, record)).join("")}</div>
        </aside>
        <div class="surface topic-workspace">${selected ? `${renderTopicEditor(selected, record)}${allTopicsReviewed ? "" : renderTopicReviewFooter(record, selected)}` : `<div class="empty">没有识别到问题主题</div>`}</div>
      </div>
      ${renderReviewDialog(record, reviewedCount, unresolvedCount)}
    </section>`;
}

function renderTopicNavigationItem(topic, index, selectedId, record) {
  const status = topicReviewState(topic);
  const confidence = topicConfidence(topic);
  const reviewed = topicIsReviewed(topic);
  const priority = topicNeedsPriorityReview(topic);
  const reasons = topicReviewReasons(topic, record);
  const reasonText = reasons.length ? `重点原因：${reasons.join("、")}` : "";
  const navigationTitle = shortTopicTitle(topic);
  return `<button class="question-nav-item ${selectedId === topic.id ? "active" : ""} ${priority ? "is-priority" : ""}" data-question="${topic.id}">
    <div class="question-nav-primary">
      <div class="question-nav-title"><span>${String(index + 1).padStart(2, "0")}</span><strong title="${escapeHtml(topic.title)}">${escapeHtml(navigationTitle)}</strong></div>
      <span class="question-nav-followups">${topic.followUps.length} 个追问</span>
    </div>
    <div class="question-nav-secondary">
      <div class="question-nav-confidence confidence-${confidence} ${reviewed ? "handled" : ""}">${confidence === "low" ? renderLucideIcon("triangle-alert") : `<span class="question-nav-confidence-dot" aria-hidden="true"></span>`}<span>识别置信度：${confidenceLabel(confidence)}</span></div>
      <span class="question-nav-review-state ${status.key}" ${reasonText ? `title="${escapeHtml(reasonText)}" aria-label="${escapeHtml(`${status.label}，${reasonText}`)}"` : ""}>${status.label}</span>
    </div>
  </button>`;
}

function shortTopicTitle(topic) {
  return String(topic?.title || "未命名主题").split(/[：:]/, 1)[0].trim() || "未命名主题";
}

function renderParseProgress(record) {
  const phases = ["inspecting", "transcribing", "validating", "structuring", "submitting", "completed"];
  const current = phases.indexOf(record.phase || "queued");
  return `<section class="page narrow">
    ${renderSteps(1)}
    <div class="page-header"><div><span class="eyebrow">PARSE AGENT</span><h1 class="page-title">正在理解面试材料</h1><p class="page-desc">${escapeHtml(record.company)} · ${escapeHtml(record.position)} · ${escapeHtml(PARSE_PHASE_LABELS[record.phase] || "任务排队")}</p></div>${record.status === "failed" ? `<button class="button" id="retryParse">重新解析</button>` : `<span class="live-indicator"><span></span>实时解析</span>`}</div>
    <div class="parse-stage-list">${phases.map((phase, index) => {
      const skipped = phase === "transcribing" && record.sourceMode !== "audio";
      const status = record.status === "failed" && phase === record.phase ? "failed" : current > index || record.phase === "completed" ? "done" : current === index ? "active" : "pending";
      return `<div class="parse-stage ${status}"><span class="parse-stage-index">${status === "done" ? "✓" : index + 1}</span><div><strong>${PARSE_PHASE_LABELS[phase]}</strong><span>${skipped ? "文字来源自动跳过" : status === "active" ? "正在处理" : status === "done" ? "已完成" : "等待中"}</span></div></div>`;
    }).join("")}</div>
    ${record.parseError ? `<div class="error">${escapeHtml(record.parseError)}</div>` : ""}
    <section class="surface agent-timeline"><div class="section-title">解析轨迹</div>${record.parseEvents?.length ? record.parseEvents.map(renderEvent).join("") : `<div class="empty"><div class="empty-title">等待 ParseAgent 事件</div><p>这里只显示阶段、工具、数量和错误，不展示模型隐藏思考。</p></div>`}</section>
  </section>`;
}

function renderTopicEditor(topic, record) {
  const root = topic.mainTurn;
  const selectedQuestionType = QUESTION_TYPES.includes(root.questionType) ? root.questionType : "其他";
  const mergeTargets = (record.topics || []).filter(item => item.id !== topic.id);
  const status = topicReviewState(topic);
  const confidence = topicConfidence(topic);
  const reviewed = topicIsReviewed(topic);
  const reviewReasons = topicReviewReasons(topic, record);
  const visibleReasons = reviewReasons.slice(0, 2);
  return `<form id="topicEditor" data-id="${topic.id}" class="section topic-editor">
    <div class="topic-editor-heading">
      <div class="topic-title-control">
        <span class="topic-title-label">当前主题</span>
        <div class="topic-title-row">
          <input name="topicTitle" data-topic-title value="${escapeHtml(topic.title)}" aria-label="主题标题" style="--topic-title-length:${Math.min(14, Math.max(4, Array.from(String(topic.title || "")).length))}" />
          <span class="topic-state prominent ${status.key}">${status.label}</span>
          ${confidence !== "high" || [root, ...(topic.followUps || [])].some(turn => turn.needsConfirmation) ? `<span class="confidence-warning confidence-${confidence} ${reviewed ? "handled" : ""}">${reviewed && confidence === "low" ? "原始识别置信度：低" : `识别置信度：${confidenceLabel(confidence)}`}</span>` : ""}
          ${visibleReasons.length ? `<span class="review-reason" title="${escapeHtml(reviewReasons.join("、"))}">${renderLucideIcon("triangle-alert")}<span>重点原因：${escapeHtml(visibleReasons.join("、"))}</span></span>` : ""}
        </div>
      </div>
      <div class="topic-heading-controls">
        <label class="topic-type-control"><span>题型</span><select class="select" name="questionType" data-topic-type>${QUESTION_TYPES.map(type => `<option ${type === selectedQuestionType ? "selected" : ""}>${type}</option>`).join("")}</select></label>
        ${mergeTargets.length ? `<details class="topic-merge">
          <summary class="button secondary compact" id="openMergeTopic">${renderLucideIcon("git-merge")}合并主题</summary>
          <div class="topic-merge-panel">
            <div class="topic-merge-heading"><strong>合并当前主题</strong><span>选择要并入的目标主题</span></div>
            <div class="topic-merge-options" role="radiogroup" aria-label="目标主题">
              ${mergeTargets.map(item => `<label class="topic-merge-option"><input type="radio" name="mergeTarget" value="${item.id}" /><span><strong>${String(record.topics.indexOf(item) + 1).padStart(2, "0")} ${escapeHtml(shortTopicTitle(item))}</strong><small>${item.followUps.length} 个追问</small></span></label>`).join("")}
            </div>
            <p class="topic-merge-note">当前主问题和 ${topic.followUps.length} 个追问将作为追问加入目标主题，原文和人工修改都会保留。${record.report ? "合并后需要重新生成报告。" : ""}</p>
            <div class="topic-merge-actions"><button type="button" class="button ghost compact" data-cancel-merge>取消</button><button type="button" class="button compact" id="mergeTopic" disabled>确认合并</button></div>
          </div>
        </details>` : ""}
      </div>
    </div>
    <div class="conversation-thread">
      <section class="main-turn">
        <div class="main-turn-heading">
          <div class="turn-kicker"><span class="thread-node root"></span><strong>主问题</strong>${root.editedQuestion || root.editedAnswer ? `<em class="turn-revision-label">人工修订</em>` : ""}</div>
          ${state.editingTurnId === root.id ? "" : `<button type="button" class="icon-button main-turn-edit" data-edit-turn="${root.id}" aria-label="编辑主问题和回答" data-tooltip="编辑主问题和回答">${renderLucideIcon("pencil")}</button>`}
        </div>
        ${renderMainTurn(root)}
      </section>
      <div class="follow-up-section">
        <div class="follow-up-section-heading"><div><strong>追问记录</strong><span>${topic.followUps.length} 条</span></div>${topic.followUps.length ? `<button type="button" class="button ghost compact" data-expand-all-followups>${allFollowUpsExpanded(topic) ? "收起全部" : "展开全部"}</button>` : ""}</div>
        ${topic.followUps.length ? `<div class="follow-up-list">${topic.followUps.map((turn, index) => renderFollowUpTurn(turn, index)).join("")}</div>` : `<div class="no-follow-ups">当前主题没有识别到追问。</div>`}
      </div>
      ${renderTopicSources(topic, record)}
    </div>
  </form>`;
}

function renderMainTurn(turn) {
  if (state.editingTurnId === turn.id) return renderTurnEditForm(turn, "主问题");
  return `<div class="turn-review" data-turn="${turn.id}">
    <p class="main-question-text">${escapeHtml(turn.interviewerQuestion || "未识别到主问题")}</p>
    <section class="main-answer">
      <span class="main-answer-label">候选人回答</span>
      <p>${escapeHtml(turn.candidateAnswer || "未识别到回答")}</p>
    </section>
  </div>`;
}

function renderTurnEditForm(turn, label) {
  return `<div class="turn-edit-form" data-turn-editor="${turn.id}">
    <label class="field"><span>${label}</span><textarea class="textarea editor-question" data-edit-field="question" required>${escapeHtml(turn.editedQuestion || turn.interviewerQuestion)}</textarea></label>
    <label class="field"><span>用于评分的回答</span><textarea class="textarea editor-answer" data-edit-field="answer">${escapeHtml(turn.editedAnswer || turn.candidateAnswer)}</textarea></label>
    <div class="edit-actions"><button type="button" class="button ghost compact" data-cancel-edit>取消</button><button type="submit" class="button secondary compact">保存修改</button></div>
  </div>`;
}

function renderFollowUpTurn(turn, index) {
  const expanded = followUpIsExpanded(turn);
  const editing = state.editingTurnId === turn.id;
  const status = turnReviewState(turn);
  const answer = turn.candidateAnswer || "未识别到回答";
  return `<section class="follow-up-turn ${expanded ? "expanded" : ""}">
    <span class="thread-node"></span>
    <button type="button" class="follow-up-question" data-toggle-followup="${turn.id}" aria-expanded="${expanded}">
      <span class="follow-up-question-copy"><small>追问 ${index + 1}</small><span class="follow-up-question-line"><strong>${escapeHtml(turn.interviewerQuestion || "未识别到追问内容")}</strong><span class="follow-up-question-meta"><span class="topic-state ${status.key}">${status.label}</span>${renderLucideIcon(expanded ? "chevron-up" : "chevron-down")}</span></span></span>
    </button>
    ${editing ? renderTurnEditForm(turn, `追问 ${index + 1}`) : expanded ? `<div class="follow-up-answer"><span>候选人回答${turn.editedAnswer ? `<em>人工修订</em>` : ""}</span><p>${escapeHtml(answer)}</p><div class="follow-up-actions"><button type="button" class="text-action" data-edit-turn="${turn.id}">${renderLucideIcon("pencil")}编辑追问</button><button type="button" class="text-action" data-split-followup="${turn.id}">${renderLucideIcon("split")}拆为独立主题</button></div></div>` : ""}
  </section>`;
}

function renderTopicReviewFooter(record, selected) {
  const index = record.topics.findIndex(item => item.id === selected.id);
  const currentReviewed = topicIsReviewed(selected);
  return `<footer class="topic-review-footer">
    <div class="review-current-topic"><strong>${topicReviewState(selected).label}</strong><span>主题 ${index + 1} / ${record.topics.length}</span></div>
    <div class="review-action-buttons">
      <button type="button" class="button secondary" data-previous-topic ${index <= 0 ? "disabled" : ""}>${renderLucideIcon("arrow-left")}上一主题</button>
      <button type="button" class="button ghost" data-skip-topic>暂时跳过</button>
      <button type="button" class="button" data-confirm-topic ${currentReviewed ? "disabled" : ""}>${renderLucideIcon("check")}${currentReviewed ? "本主题已确认" : "确认本主题"}</button>
    </div>
  </footer>`;
}

function renderReviewDialog(record, reviewedCount, unresolvedCount) {
  const unconfirmedCount = Math.max(0, record.topics.length - reviewedCount);
  const requiresAcknowledgement = unconfirmedCount > 0 || unresolvedCount > 0;
  const acknowledged = !requiresAcknowledgement || state.acknowledgeUnreviewed;
  const modeSelected = state.reviewMode === "local" || state.reviewMode === "web";
  const webAvailable = Boolean(state.health?.webVerifyAvailable);
  const canStart = acknowledged && modeSelected && (state.reviewMode !== "web" || webAvailable);
  const modeDescription = state.reviewMode === "web"
    ? "本地证据不足或信息需要核实时，Agent 才会联网搜索。"
    : state.reviewMode === "local"
      ? "仅使用面试稿、JD、简历和本地知识库，不访问互联网。"
      : "请选择本次复盘使用的资料范围。";
  const startLabel = state.loading
    ? "正在创建任务..."
    : unconfirmedCount
      ? "启动快速复盘"
      : "启动 Agent 复盘";
  const acknowledgementText = unconfirmedCount && unresolvedCount
    ? "我已了解未校对题卡和未归类内容会在报告中标记"
    : unconfirmedCount
      ? "我已了解未校对内容会在报告中标记"
      : "我已了解未归类内容会在报告中标记";
  return `<dialog class="review-dialog" id="reviewDialog" aria-labelledby="reviewDialogTitle">
    <div class="review-dialog-header">
      <h2 id="reviewDialogTitle">开始 Agent 复盘</h2>
      <button type="button" class="icon-button" data-close-review-dialog aria-label="关闭复盘设置">${renderLucideIcon("x")}</button>
    </div>
    <div class="review-dialog-body">
      ${state.error ? `<div class="error review-dialog-error">${escapeHtml(state.error)}</div>` : ""}
      <div class="review-dialog-status ${requiresAcknowledgement ? "warning" : "success"}">
        <span class="review-dialog-status-icon" aria-hidden="true">${requiresAcknowledgement ? "!" : renderLucideIcon("circle-check")}</span>
        <strong>${unconfirmedCount ? `还有 ${unconfirmedCount} 个题卡未经校对，将使用当前解析结果。` : unresolvedCount ? `题卡均已确认，另有 ${unresolvedCount} 项未归类内容。` : `${record.topics.length} 个题卡均已确认。`}</strong>
        ${unconfirmedCount ? `<button type="button" class="text-action" data-continue-review>继续校对</button>` : ""}
      </div>
      <fieldset class="review-mode-fieldset">
        <legend>复盘资料范围 <span>必选</span></legend>
        <div class="review-mode-options">
          <label class="review-mode-option ${state.reviewMode === "local" ? "selected" : ""}">
            <input type="radio" name="reviewMode" value="local" ${state.reviewMode === "local" ? "checked" : ""} />
            <span><strong>仅使用内部资料</strong><small>不访问互联网</small></span>
          </label>
          <label class="review-mode-option ${state.reviewMode === "web" ? "selected" : ""} ${webAvailable ? "" : "unavailable"}">
            <input type="radio" name="reviewMode" value="web" ${state.reviewMode === "web" ? "checked" : ""} ${webAvailable ? "" : "disabled"} />
            <span><strong>必要时联网核验</strong><small>${webAvailable ? "仅在需要核实时搜索" : "服务当前不可用"}</small></span>
          </label>
        </div>
        <p class="review-mode-description" id="reviewModeDescription">${modeDescription}</p>
      </fieldset>
      ${requiresAcknowledgement ? `<label class="review-dialog-ack"><input id="acknowledgeUnreviewed" type="checkbox" ${state.acknowledgeUnreviewed ? "checked" : ""} /><span>${acknowledgementText}</span></label>` : ""}
    </div>
    <div class="review-dialog-footer">
      <button type="button" class="button ghost" data-close-review-dialog>取消</button>
      <button type="button" class="button" id="startRun" aria-describedby="reviewModeDescription" ${state.loading || !canStart ? "disabled" : ""}>${renderLucideIcon("arrow-right")}${startLabel}</button>
    </div>
  </dialog>`;
}

function renderTopicSources(topic, record) {
  const turns = [topic.mainTurn, ...(topic.followUps || [])];
  const segmentIds = new Set(turns.flatMap(turn => [...(turn.questionSegmentIds || []), ...(turn.answerSegmentIds || [])]));
  return `<section class="topic-sources" id="topic-sources">
    <div class="topic-sources-heading"><div><strong>本主题文字来源</strong><span>主问题与全部追问原文 · 只读</span></div><span>${segmentIds.size} 个原始片段</span></div>
    ${record.audio ? `<div class="topic-source-audio"><div><strong>${escapeHtml(record.audio.filename || "面试音频")}</strong><span>点击片段时间可从对应位置播放</span></div><audio id="sourceAudio" controls preload="metadata" src="${escapeHtml(record.audio.url)}"></audio></div>` : ""}
    <div class="topic-source-turns">${turns.map((turn, index) => renderTopicSourceTurn(turn, index === 0 ? "主问题" : `追问 ${index}`, record)).join("")}</div>
  </section>`;
}

function renderTopicSourceTurn(turn, label, record) {
  return `<section class="topic-source-turn">
    <div class="topic-source-turn-label"><strong>${label}</strong></div>
    <div class="topic-source-lines">
      ${renderTopicSourceLines(record, turn.questionSegmentIds, turn.extractedQuestion || turn.interviewerQuestion, "interviewer")}
      ${renderTopicSourceLines(record, turn.answerSegmentIds, turn.extractedAnswer || turn.candidateAnswer, "candidate")}
    </div>
  </section>`;
}

function renderTopicSourceLines(record, ids = [], fallback = "", fallbackRole = "unknown") {
  const byId = new Map((record.segments || []).map(segment => [segment.id, segment]));
  const segments = ids.map(id => byId.get(id)).filter(Boolean);
  if (!segments.length && fallback) {
    segments.push({ id: "", ordinal: 0, rawText: fallback, speakerRole: fallbackRole, speakerLabel: "抽取文本" });
  }
  if (!segments.length) return `<div class="topic-source-empty">未找到对应原始片段</div>`;
  return segments.map(segment => {
    const role = { interviewer: "面试官", candidate: "候选人", system_noise: "系统/噪声", unknown: "未知" }[segment.speakerRole] || "未知";
    const locator = segment.startTime == null ? (segment.ordinal ? `S${String(segment.ordinal).padStart(4, "0")}` : "抽取文本") : formatDuration(segment.startTime);
    const locatorElement = record.audio && segment.startTime != null ? `<button type="button" class="topic-source-locator" data-seek="${segment.startTime}">${locator}</button>` : `<span class="topic-source-locator">${locator}</span>`;
    return `<div class="topic-source-line"><span class="topic-source-role ${escapeHtml(segment.speakerRole || "unknown")}">${role}</span><p>${escapeHtml(segment.rawText || "")}</p>${locatorElement}</div>`;
  }).join("");
}

function topicIsReviewed(topic) {
  return [topic.mainTurn, ...(topic.followUps || [])].every(turn => Boolean(turn.confirmed));
}

function topicTurns(topic) {
  return [topic.mainTurn, ...(topic.followUps || [])];
}

function topicConfidence(topic) {
  const turns = topicTurns(topic);
  const levels = turns.map(turn => turn.confidence || "medium");
  if (levels.includes("low")) return "low";
  if (levels.includes("medium") || turns.some(turn => turn.needsConfirmation)) return "medium";
  return "high";
}

function topicNeedsPriorityReview(topic) {
  if (topicIsReviewed(topic)) return false;
  const turns = topicTurns(topic);
  return topicConfidence(topic) === "low" || turns.some(turn => turn.needsConfirmation);
}

function topicReviewReasons(topic, record) {
  if (topicIsReviewed(topic)) return [];
  const segmentMap = new Map((record?.segments || []).map(segment => [segment.id, segment]));
  const reasons = [];
  topicTurns(topic).forEach(turn => {
    if (turn.confirmed) return;
    const turnReasons = [];
    if (!String(turn.interviewerQuestion || "").trim()) turnReasons.push("问题内容缺失");
    if (!String(turn.candidateAnswer || "").trim()) turnReasons.push("未识别到回答");
    if (turn.confidence === "low") turnReasons.push("识别置信度低");
    const segmentIds = [...(turn.questionSegmentIds || []), ...(turn.answerSegmentIds || [])];
    const segments = segmentIds.map(id => segmentMap.get(id)).filter(Boolean);
    if (segments.some(segment => segment.speakerRole === "unknown")) turnReasons.push("说话人待确认");
    if (segments.some(segment => segment.needsConfirmation)) turnReasons.push("原始片段质量待确认");
    if (["conflict", "partial", "unverified"].includes(turn.provenanceStatus)) turnReasons.push("分块或引用结果冲突");
    if (turn.needsConfirmation && !turnReasons.length) turnReasons.push("问答边界或分类待确认");
    reasons.push(...turnReasons);
  });
  return [...new Set(reasons)];
}

function topicReviewState(topic) {
  const turns = topicTurns(topic);
  const edited = turns.some(turn => turn.editedQuestion || turn.editedAnswer || turn.provenanceStatus === "edited");
  if (topicIsReviewed(topic)) return { key: edited ? "modified" : "confirmed", label: edited ? "已修改" : "已确认" };
  if (topic.reviewStatus === "skipped") return { key: "skipped", label: "稍后处理" };
  if (topicNeedsPriorityReview(topic)) return { key: "attention", label: "需要重点校对" };
  return { key: "unchecked", label: "未校对" };
}

function turnReviewState(turn) {
  if (turn.confirmed) return { key: turn.editedQuestion || turn.editedAnswer ? "modified" : "confirmed", label: turn.editedQuestion || turn.editedAnswer ? "已修改" : "已确认" };
  if (turn.needsConfirmation || turn.confidence === "low") return { key: "attention", label: "需要重点校对" };
  return { key: "unchecked", label: "未校对" };
}

function followUpIsExpanded(turn) {
  if (state.editingTurnId === turn.id) return true;
  if (state.collapsedFollowUpIds.includes(turn.id)) return false;
  return state.expandedFollowUpIds.includes(turn.id);
}

function allFollowUpsExpanded(topic) {
  return topic.followUps.every(followUpIsExpanded);
}

function renderRunPage() {
  const record = currentRecord();
  if (!record) return renderMissing();
  const phase = record.phase || "queued";
  const terminal = ["completed", "failed", "cancelled"].includes(record.status);
  const progress = record.runProgress || {};
  const completedTopics = Number(progress.completedTopics || progress.checkpoint?.completedTopicIds?.length || 0);
  const totalTopics = Number(record.topics?.length || record.questionCount || 0);
  const auditRound = Number(progress.auditRound || 0);
  const revisionCount = Number(progress.revisionCount || 0);
  const checkpoint = progress.checkpoint || {};
  const modeLabel = record.agentMode === "fixture" ? "Fixture 模拟" : record.degraded ? "确定性降级" : "HelloAgents 实时";
  let stageStatus = {};
  if (record.status === "failed") {
    const evidenceComplete = Boolean(checkpoint.evidenceComplete) || (totalTopics > 0 && completedTopics >= totalTopics);
    const auditAccepted = Boolean(checkpoint.auditAccepted);
    stageStatus = {
      evidence_review: evidenceComplete ? "done" : "error",
      reflection_audit: !evidenceComplete ? "pending" : auditAccepted ? "done" : "error",
      growth_plan: !auditAccepted ? "pending" : "error"
    };
  }
  return `
    <section class="page">
      ${renderSteps(3)}
      <div class="page-header"><div><span class="eyebrow">AGENT WORKFLOW</span><h1 class="page-title">多 Agent 正在复盘</h1><p class="page-desc">${escapeHtml(record.company)} · ${escapeHtml(record.position)} · ${escapeHtml(PHASE_LABELS[phase] || phase)}</p></div><div class="run-header-actions">${record.status === "failed" ? `<button class="button secondary" id="fallbackRun">生成降级报告</button><button class="button" id="resumeRun">从检查点恢复</button>` : terminal && record.status === "completed" ? `<button class="button" id="openReport">查看报告</button>` : `<span class="live-indicator"><span></span>实时执行</span>`}</div></div>
      <div class="agent-run-summary"><span class="agent-mode-mark ${record.agentMode || "helloagents"} ${record.degraded ? "degraded" : ""}">${escapeHtml(modeLabel)}</span><div><strong>${completedTopics}/${totalTopics} 个主题已提交</strong><span>Reflection 审计 ${auditRound}/2 轮 · 已修订 ${revisionCount} 次</span></div></div>
      ${record.status === "failed" ? `<div class="run-failure"><strong>${escapeHtml(record.failureCode || "AGENT_FAILED")}</strong><span>${escapeHtml(record.runError || "Agent 阶段未完成，可从最近检查点恢复或主动生成降级报告。")}</span></div>` : ""}
      <div class="agent-stage-grid">
        ${agentStage("evidence_review", "ReAct", "证据诊断", phase, `${completedTopics}/${totalTopics} 个主题`, stageStatus.evidence_review)}
        ${agentStage("reflection_audit", "Reflection", "质量审计", phase, `${auditRound}/2 轮 · ${revisionCount} 次修订`, stageStatus.reflection_audit)}
        ${agentStage("growth_plan", "PlanSolve", "成长计划", phase, checkpoint.growthComplete ? "七天计划已提交" : "等待已审计报告", stageStatus.growth_plan)}
      </div>
      <section class="surface agent-timeline">
        <div class="section-title">执行轨迹</div>
        ${state.events.length ? state.events.map(renderEvent).join("") : `<div class="empty"><div class="empty-title">等待任务事件</div><p>连接建立后会显示阶段和工具状态，不展示模型隐藏思考。</p></div>`}
      </section>
    </section>`;
}

function agentStage(key, agent, title, phase, detail = "", statusOverride = "") {
  const order = ["queued", "evidence_review", "reflection_audit", "growth_plan", "completed"];
  const current = order.indexOf(phase);
  const target = order.indexOf(key);
  const status = statusOverride || (current > target ? "done" : current === target ? "active" : "pending");
  const statusLabel = { done: "已完成", active: "执行中", error: "执行失败", pending: "等待中" }[status] || "等待中";
  return `<div class="agent-stage ${status}"><span class="agent-kicker">${agent}</span><strong>${title}</strong><span>${statusLabel}${detail ? ` · ${escapeHtml(detail)}` : ""}</span></div>`;
}

function renderEvent(event) {
  const data = event.data || {};
  const title = data.agent ? AGENT_LABELS[data.agent] || data.agent : data.tool || EVENT_LABELS[event.type] || event.type.replaceAll("_", " ");
  const metrics = [
    data.segmentCount != null ? `${data.segmentCount} 个片段` : "",
    data.questionCount != null ? `${data.questionCount} 个问题` : "",
    data.chunkCount != null ? `${data.chunkCount} 个分块` : "",
    data.issueCount != null ? `${data.issueCount} 个校验项` : "",
    data.retryCount ? `重试 ${data.retryCount} 次` : "",
    data.completed != null && data.total != null ? `${data.completed}/${data.total} 个主题` : "",
    data.round != null ? `第 ${data.round} 轮` : "",
    data.findingCount != null ? `${data.findingCount} 条发现` : "",
    data.revisionCount != null ? `${data.revisionCount} 次修订` : "",
    data.durationSeconds != null ? `耗时 ${Number(data.durationSeconds).toFixed(2)} 秒` : ""
  ].filter(Boolean).join(" · ");
  const detail = data.message || (data.hits != null ? `检索并校验 ${data.hits} 条证据` : metrics || data.status || "阶段事件已记录");
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
  const quickReview = interview.reviewMode === "quick";
  const provenanceLabel = interview.degraded ? "确定性降级报告" : interview.agentMode === "fixture" ? "Fixture 模拟报告" : "HelloAgents Agent 生成";
  const provenanceClass = interview.degraded ? "degraded" : interview.agentMode === "fixture" ? "fixture" : "agent";
  return `
    <section class="page report-page">
      ${renderSteps(4)}
      <div class="page-header"><div><span class="eyebrow">EVIDENCE REVIEW</span><h1 class="page-title">${escapeHtml(interview.company)} · ${escapeHtml(interview.position)}</h1><p class="page-desc">${escapeHtml(interview.round || "未填写轮次")} · ${escapeHtml(modeLabel(interview.analysisMode))}</p></div><div class="filters"><button class="button secondary" data-nav="#/parse/${record.id}">编辑题卡</button><button class="button" id="exportReport">导出 Markdown</button></div></div>
      <div class="summary-band"><div class="summary-heading"><div><span class="eyebrow">整场结论</span><h2>岗位信号与回答质量</h2></div><div class="report-badges"><span class="mode-badge ${provenanceClass}">${escapeHtml(provenanceLabel)}</span><span class="mode-badge ${quickReview ? "quick" : ""}">${quickReview ? "快速复盘 · 含未校对题卡" : "证据已审计"}</span></div></div><p>${escapeHtml(interview.summary || "暂无总结")}</p></div>
      ${renderScoreSection(interview.overallScores)}
      ${renderRiskSection(interview.topRisks || [])}
      <section class="report-band"><div class="band-header"><div><span class="eyebrow">ACTION PLAN</span><h2>七天行动计划</h2></div></div><div class="action-list">${actions.map(action => renderAction(record, action)).join("")}</div></section>
      <section class="report-band"><div class="band-header"><div><span class="eyebrow">QUESTION REVIEW</span><h2>逐题证据复盘</h2></div></div><div class="accordion">${questions.map(renderQuestionReview).join("")}</div></section>
      <section class="report-band audit-band"><div class="band-header"><div><span class="eyebrow">REFLECTION AUDIT</span><h2>质量审计</h2></div><span class="audit-revision-count">修订 ${Number(interview.auditRevisionCount || 0)} 次</span></div><ul>${(interview.auditNotes || []).map(note => `<li>${escapeHtml(note)}</li>`).join("")}</ul></section>
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
    ${reviewBlock("主题主回答", question.extractedAnswer || question.candidateAnswer)}
    ${(question.followUpTurns || []).length ? `<div class="review-block"><h4>追问对主题评分的影响</h4><div class="follow-up-review-list">${question.followUpTurns.map((turn, index) => `<div class="follow-up-review"><div><strong>追问 ${index + 1}：${escapeHtml(turn.interviewerQuestion)}</strong><span class="tag follow-up-impact">${escapeHtml(turn.followUpImpact || "待判断")}</span></div><p>${escapeHtml(turn.candidateAnswer || "未识别到回答")}</p></div>`).join("")}</div></div>` : ""}
    ${reviewBlock("AI 诊断", question.diagnosis)}
    <div class="review-block"><h4>评分证据</h4><div class="evidence-list">${(question.scoreEvidence || []).map(item => `<div class="evidence-row"><span class="tag">${escapeHtml(SCORE_LABELS[item.dimension] || item.dimension)}${item.level ? ` · ${escapeHtml(item.level)}` : ""} · ${Number(item.score || 0).toFixed(1)}</span><p>${escapeHtml(item.rationale || "")}</p>${item.quote ? `<blockquote>“${escapeHtml(item.quote)}”</blockquote>` : `<span class="small">本维度没有可直接引用的原文。</span>`}</div>`).join("")}</div></div>
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
  document.querySelectorAll("[data-delete]").forEach(button => button.addEventListener("click", async () => {
    if (!confirm("将删除本机中的面试材料、音频、题卡和报告，确定继续吗？")) return;
    try {
      await api(`/api/v1/interviews/${button.dataset.delete}`, { method: "DELETE" });
      state.records = state.records.filter(item => item.id !== button.dataset.delete);
      persistRecords();
      render();
    } catch (error) {
      setError(readableError(error));
    }
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
  document.querySelectorAll("[data-transcript-mode]").forEach(button => button.addEventListener("click", () => setTranscriptMode(button.dataset.transcriptMode)));
  document.querySelectorAll("[data-date-picker]").forEach(picker => {
    const textInput = document.querySelector(`[name="${picker.dataset.datePicker}"]`);
    picker.addEventListener("change", () => {
      if (textInput) textInput.value = picker.value;
    });
    textInput?.addEventListener("input", () => {
      if (/^\d{4}-\d{2}-\d{2}$/.test(textInput.value)) picker.value = textInput.value;
    });
  });
}

function setTranscriptMode(mode) {
  const form = document.querySelector("#newForm");
  if (!form || !["paste", "text", "audio"].includes(mode)) return;
  form.elements.transcriptMode.value = mode;
  document.querySelectorAll("[data-transcript-mode]").forEach(button => {
    const active = button.dataset.transcriptMode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll("[data-source-panel]").forEach(panel => panel.classList.toggle("hidden", panel.dataset.sourcePanel !== mode));
  if (form.elements.cloudConsent) form.elements.cloudConsent.required = mode === "audio";
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
  const datePicker = document.querySelector('[data-date-picker="interviewDate"]');
  if (datePicker) datePicker.value = demo.interviewDate;
  setTranscriptMode("paste");
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
  let id = "";
  try {
    const sourceMode = String(data.get("transcriptMode") || "paste");
    const transcript = String(data.get("rawTranscript") || "").trim();
    const transcriptFile = data.get("transcriptFile");
    const audioFile = data.get("audioFile");
    if (sourceMode === "paste" && !transcript) throw new Error("请粘贴面试文字稿");
    if (sourceMode === "text" && !transcriptFile?.size) throw new Error("请选择 TXT 面试文字稿");
    if (sourceMode === "audio" && !audioFile?.size) throw new Error("请选择面试音频");
    if (sourceMode === "audio" && !data.get("cloudConsent")) throw new Error("请先确认音频授权与 Deepgram 云端转写说明");
    id = crypto.randomUUID();
    const payload = {
      id,
      company: String(data.get("company") || "").trim(),
      position: String(data.get("position") || "").trim(),
      round: String(data.get("round") || "").trim(),
      interviewDate: String(data.get("interviewDate") || ""),
      reviewGoal: String(data.get("reviewGoal") || "").trim(),
      jobDescription: String(data.get("jobDescription") || "").trim(),
      resumeText: String(data.get("resumeText") || "").trim(),
      rawTranscript: sourceMode === "paste" ? transcript : "",
      analysisMode: data.get("jobDescription") || data.get("jdFile")?.size ? (data.get("resumeText") || data.get("resumeFile")?.size ? "full_context" : "job_context") : "general"
    };
    await api("/api/v1/interviews", { method: "POST", body: payload });
    await uploadOptional(id, "job_description", data.get("jdFile"));
    await uploadOptional(id, "resume", data.get("resumeFile"));
    if (sourceMode === "paste") {
      await api(`/api/v1/interviews/${id}/materials/text`, { method: "POST", body: { material_type: "transcript", text: transcript, filename: "pasted-transcript.txt" } });
    } else if (sourceMode === "text") {
      await uploadOptional(id, "transcript", transcriptFile);
    } else {
      await uploadOptional(id, "transcript_audio", audioFile, { cloudConsent: true });
    }
    const parsed = await api(`/api/v1/interviews/${id}/parse`, { method: "POST" });
    const record = normalizeInterviewRecord({
      ...payload,
      sourceMode,
      status: "parsing",
      phase: "queued",
      parseRunId: parsed.parseRunId,
      parseEvents: [],
      questions: [],
      questionCount: 0
    });
    saveRecord(record);
    state.loading = false;
    location.hash = `#/parse/${id}`;
  } catch (error) {
    if (id) await api(`/api/v1/interviews/${id}`, { method: "DELETE" }).catch(() => {});
    state.loading = false;
    state.error = readableError(error);
    render();
  }
}

async function uploadOptional(interviewId, materialType, file, options = {}) {
  if (!file?.size) return;
  const body = new FormData();
  body.append("material_type", materialType);
  body.append("file", file);
  if (options.cloudConsent) body.append("cloud_consent", "true");
  await api(`/api/v1/interviews/${interviewId}/materials`, { method: "POST", body, rawBody: true });
}

function bindParsePage() {
  document.querySelectorAll("[data-review-filter]").forEach(button => button.addEventListener("click", () => {
    state.reviewNavFilter = button.dataset.reviewFilter;
    const record = currentRecord();
    const firstVisible = state.reviewNavFilter === "priority" ? record?.topics?.find(topicNeedsPriorityReview) : record?.topics?.[0];
    if (firstVisible) state.selectedQuestionId = firstVisible.id;
    state.editingTurnId = null;
    render();
  }));
  document.querySelectorAll("[data-question]").forEach(button => button.addEventListener("click", () => {
    state.selectedQuestionId = button.dataset.question;
    state.editingTurnId = null;
    render();
  }));
  document.querySelector("#topicEditor")?.addEventListener("submit", event => {
    event.preventDefault();
    const record = currentRecord();
    const topic = record?.topics?.find(item => item.id === event.currentTarget.dataset.id);
    if (!topic) return;
    const data = new FormData(event.currentTarget);
    topic.title = String(data.get("topicTitle") || topic.title).trim();
    topic.mainTurn.topicTitle = topic.title;
    topic.mainTurn.questionType = String(data.get("questionType") || "其他");
    event.currentTarget.querySelectorAll("[data-turn-editor]").forEach(editor => {
      const turn = findQuestion(record, editor.dataset.turnEditor);
      if (!turn) return;
      const question = editor.querySelector('[data-edit-field="question"]')?.value.trim() || "";
      const answer = editor.querySelector('[data-edit-field="answer"]')?.value.trim() || "";
      turn.editedQuestion = question === turn.extractedQuestion ? "" : question;
      turn.editedAnswer = answer === turn.extractedAnswer ? "" : answer;
      turn.interviewerQuestion = question;
      turn.candidateAnswer = answer;
      turn.provenanceStatus = turn.editedQuestion || turn.editedAnswer ? "edited" : "source";
      turn.needsConfirmation = !question;
      turn.confirmed = false;
    });
    topic.reviewStatus = "unchecked";
    record.updatedAt = new Date().toISOString();
    syncFlatQuestions(record);
    saveRecord(record);
    state.editingTurnId = null;
    toast("修改已保存，请重新确认本主题");
    render();
  });
  const topicTitleInput = document.querySelector("[data-topic-title]");
  topicTitleInput?.addEventListener("input", event => {
    const length = Array.from(event.currentTarget.value.trim()).length;
    event.currentTarget.style.setProperty("--topic-title-length", Math.min(14, Math.max(4, length)));
  });
  topicTitleInput?.addEventListener("change", event => updateTopicMetadata({ title: event.currentTarget.value }));
  document.querySelector("[data-topic-type]")?.addEventListener("change", event => updateTopicMetadata({ questionType: event.currentTarget.value }));
  document.querySelectorAll("[data-edit-turn]").forEach(button => button.addEventListener("click", () => { state.editingTurnId = button.dataset.editTurn; render(); }));
  document.querySelectorAll("[data-cancel-edit]").forEach(button => button.addEventListener("click", () => { state.editingTurnId = null; render(); }));
  document.querySelectorAll("[data-toggle-followup]").forEach(button => button.addEventListener("click", () => toggleFollowUp(button.dataset.toggleFollowup)));
  document.querySelector("[data-expand-all-followups]")?.addEventListener("click", toggleAllFollowUps);
  document.querySelector("[data-previous-topic]")?.addEventListener("click", () => navigateTopic(-1));
  document.querySelector("[data-skip-topic]")?.addEventListener("click", skipCurrentTopic);
  document.querySelector("[data-confirm-topic]")?.addEventListener("click", confirmCurrentTopic);
  document.querySelectorAll("[data-split-followup]").forEach(button => button.addEventListener("click", () => splitFollowUp(button.dataset.splitFollowup)));
  document.querySelectorAll('input[name="mergeTarget"]').forEach(input => input.addEventListener("change", event => {
    document.querySelectorAll(".topic-merge-option").forEach(option => option.classList.toggle("selected", option.contains(event.currentTarget)));
    const confirmButton = document.querySelector("#mergeTopic");
    if (confirmButton) confirmButton.disabled = false;
  }));
  document.querySelector("[data-cancel-merge]")?.addEventListener("click", () => document.querySelector(".topic-merge")?.removeAttribute("open"));
  document.querySelector("#mergeTopic")?.addEventListener("click", mergeSelectedTopic);
  document.querySelectorAll("[data-seek]").forEach(button => button.addEventListener("click", () => seekAudio(button.dataset.seek)));
  document.querySelectorAll('input[name="reviewMode"]').forEach(input => input.addEventListener("change", event => {
    state.reviewMode = event.currentTarget.value;
    state.error = "";
    render();
  }));
  document.querySelector("#acknowledgeUnreviewed")?.addEventListener("change", event => {
    state.acknowledgeUnreviewed = event.currentTarget.checked;
    state.acknowledgeUnresolved = event.currentTarget.checked;
    render();
  });
  document.querySelector("#openReviewDialog")?.addEventListener("click", openReviewDialog);
  document.querySelectorAll("[data-close-review-dialog]").forEach(button => button.addEventListener("click", closeReviewDialog));
  document.querySelector("[data-continue-review]")?.addEventListener("click", () => {
    closeReviewDialog();
    locatePendingItem();
  });
  const reviewDialog = document.querySelector("#reviewDialog");
  reviewDialog?.addEventListener("close", () => { state.reviewDialogOpen = false; });
  reviewDialog?.addEventListener("click", event => { if (event.target === reviewDialog) closeReviewDialog(); });
  document.querySelector("#startRun")?.addEventListener("click", startAgentRun);
  document.querySelector("#retryParse")?.addEventListener("click", retryParse);
}

function openReviewDialog() {
  state.reviewDialogOpen = true;
  state.error = "";
  const dialog = document.querySelector("#reviewDialog");
  if (dialog && !dialog.open) dialog.showModal();
}

function closeReviewDialog() {
  state.reviewDialogOpen = false;
  const dialog = document.querySelector("#reviewDialog");
  if (dialog?.open) dialog.close();
}

function updateTopicMetadata(changes) {
  const record = currentRecord();
  const topic = record?.topics?.find(item => item.id === state.selectedQuestionId);
  if (!topic) return;
  if (changes.title != null) {
    topic.title = String(changes.title || topic.title).trim();
    topic.mainTurn.topicTitle = topic.title;
  }
  if (changes.questionType != null) topic.mainTurn.questionType = String(changes.questionType || "其他");
  [topic.mainTurn, ...topic.followUps].forEach(turn => { turn.confirmed = false; });
  topic.reviewStatus = "unchecked";
  syncFlatQuestions(record);
  saveRecord(record);
  render();
}

function toggleFollowUp(questionId) {
  const expanded = new Set(state.expandedFollowUpIds);
  const collapsed = new Set(state.collapsedFollowUpIds);
  const turn = findQuestion(currentRecord(), questionId);
  if (turn && followUpIsExpanded(turn)) {
    expanded.delete(questionId);
    collapsed.add(questionId);
  } else {
    collapsed.delete(questionId);
    expanded.add(questionId);
  }
  state.expandedFollowUpIds = [...expanded];
  state.collapsedFollowUpIds = [...collapsed];
  render();
}

function toggleAllFollowUps() {
  const topic = currentRecord()?.topics?.find(item => item.id === state.selectedQuestionId);
  if (!topic) return;
  const expanded = new Set(state.expandedFollowUpIds);
  const collapsed = new Set(state.collapsedFollowUpIds);
  const shouldCollapse = allFollowUpsExpanded(topic);
  topic.followUps.forEach(turn => {
    if (shouldCollapse) {
      expanded.delete(turn.id);
      collapsed.add(turn.id);
    } else {
      collapsed.delete(turn.id);
      expanded.add(turn.id);
    }
  });
  state.expandedFollowUpIds = [...expanded];
  state.collapsedFollowUpIds = [...collapsed];
  render();
}

function navigateTopic(offset) {
  const record = currentRecord();
  const index = record?.topics?.findIndex(item => item.id === state.selectedQuestionId) ?? -1;
  const target = record?.topics?.[index + offset];
  if (!target) return;
  state.selectedQuestionId = target.id;
  state.editingTurnId = null;
  render();
}

function skipCurrentTopic() {
  const record = currentRecord();
  const topic = record?.topics?.find(item => item.id === state.selectedQuestionId);
  if (!record || !topic) return;
  topic.reviewStatus = "skipped";
  saveRecord(record);
  const index = record.topics.indexOf(topic);
  const target = record.topics.slice(index + 1).find(item => !topicIsReviewed(item)) || record.topics[index + 1];
  if (target) state.selectedQuestionId = target.id;
  state.editingTurnId = null;
  render();
}

function confirmCurrentTopic() {
  const record = currentRecord();
  const topic = record?.topics?.find(item => item.id === state.selectedQuestionId);
  if (!record || !topic) return;
  if (state.editingTurnId) {
    setError("请先保存或取消正在编辑的内容");
    return;
  }
  const turns = [topic.mainTurn, ...topic.followUps];
  if (turns.some(turn => !String(turn.interviewerQuestion || "").trim())) {
    setError("当前主题仍有空的问题内容，请先补充后再确认");
    return;
  }
  turns.forEach(turn => {
    turn.confirmed = true;
    turn.needsConfirmation = false;
  });
  topic.reviewStatus = "confirmed";
  syncFlatQuestions(record);
  saveRecord(record);
  state.error = "";
  const index = record.topics.indexOf(topic);
  const target = record.topics.slice(index + 1).find(item => !topicIsReviewed(item)) || record.topics[index + 1];
  if (target) state.selectedQuestionId = target.id;
  const allTopicsReviewed = record.topics.every(topicIsReviewed);
  toast(allTopicsReviewed ? "全部题卡已确认，可从页面上方开始复盘" : "本主题已确认");
  render();
}

function locatePendingItem() {
  const record = currentRecord();
  if (!record) return;
  const pending = (record.topics || [])
    .filter(topic => !topicIsReviewed(topic))
    .map((topic, index) => ({
      topic,
      index,
      rank: topicNeedsPriorityReview(topic) ? 0 : topicConfidence(topic) === "medium" ? 1 : 2
    }))
    .sort((left, right) => left.rank - right.rank || left.index - right.index)[0]?.topic;
  if (pending) {
    state.selectedQuestionId = pending.id;
    state.editingTurnId = null;
    render();
    requestAnimationFrame(() => document.querySelector("#topicEditor")?.scrollIntoView({ behavior: "smooth", block: "start" }));
    return;
  }
  openReviewDialog();
}

async function startAgentRun() {
  const record = currentRecord();
  if (!record?.questions?.length) return;
  const allTopicsReviewed = record.topics?.every(topicIsReviewed);
  const reviewMode = allTopicsReviewed ? "full" : "quick";
  if (reviewMode === "quick" && !state.acknowledgeUnreviewed) {
    setError("请确认使用未经人工校对的题卡进行快速复盘");
    return;
  }
  const acknowledgeUnresolved = state.acknowledgeUnresolved;
  if ((record.unresolvedCount || record.questions.some(item => item.needsConfirmation)) && !acknowledgeUnresolved) {
    setError("请处理待确认片段，或勾选显式确认后再启动复盘");
    return;
  }
  if (state.reviewMode !== "local" && state.reviewMode !== "web") {
    setError("请选择本次复盘使用本地材料，还是允许必要时联网核验");
    return;
  }
  if (state.reviewMode === "web" && !state.health?.webVerifyAvailable) {
    setError("联网核验服务当前不可用，请改用仅本地材料");
    return;
  }
  const enableWebVerify = state.reviewMode === "web";
  state.loading = true;
  state.error = "";
  render();
  try {
    await api(`/api/v1/interviews/${record.id}/segments`, {
      method: "PATCH",
      body: { segments: (record.segments || []).map(item => ({ id: item.id, speakerRole: item.speakerRole, needsConfirmation: item.needsConfirmation, excluded: item.excluded })) }
    });
    await api(`/api/v1/interviews/${record.id}/questions`, { method: "PATCH", body: { questions: record.questions } });
    if (reviewMode === "full") {
      await api(`/api/v1/interviews/${record.id}/confirm`, { method: "POST", body: { acknowledgeUnresolved } });
    }
    const run = await api(`/api/v1/interviews/${record.id}/review-runs`, {
      method: "POST",
      body: { enableWebVerify, reviewMode, acknowledgeUnreviewed: state.acknowledgeUnreviewed, acknowledgeUnresolved }
    });
    record.runId = run.id;
    record.reviewMode = reviewMode;
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

async function loadParseResult(record) {
  const result = await api(`/api/v1/interviews/${record.id}/segments`);
  record.segments = result.segments || [];
  record.topics = (result.topics || []).map(topic => ({
    ...topic,
    mainTurn: normalizeQuestionRecord(topic.mainTurn),
    followUps: (topic.followUps || []).map(normalizeQuestionRecord)
  }));
  record.audio = result.audio || null;
  record.unresolvedCount = Number(result.unresolvedCount || 0);
  record.status = "waiting_confirmation";
  record.phase = "completed";
  record.parseError = "";
  syncFlatQuestions(record);
  if (!state.selectedQuestionId && record.topics.length) state.selectedQuestionId = record.topics[0].id;
}

function connectParseEventSource() {
  const record = currentRecord();
  if (!record?.parseRunId || activeSource || record.status !== "parsing") return;
  activeSource = new EventSource(`/api/v1/parse-runs/${record.parseRunId}/events`);
  PARSE_EVENT_TYPES.forEach(type => activeSource.addEventListener(type, async event => {
    const events = record.parseEvents || (record.parseEvents = []);
    const item = { id: Number(event.lastEventId || events.length + 1), type, data: JSON.parse(event.data || "{}"), createdAt: new Date().toISOString() };
    if (!events.some(existing => existing.id === item.id)) events.push(item);
    if (type === "PARSE_PHASE_STARTED") record.phase = item.data.phase;
    if (type === "PARSE_FAILED") {
      record.status = "failed";
      record.phase = "failed";
      record.parseError = item.data.message || "解析失败";
      closeEventSource();
    }
    if (type === "PARSE_FINISHED") {
      closeEventSource();
      await loadParseResult(record);
    }
    saveRecord(record);
    render();
  }));
  activeSource.onerror = () => {
    if (record.status === "parsing") console.warn("Parse SSE connection interrupted; EventSource will retry.");
  };
}

async function retryParse() {
  const record = currentRecord();
  if (!record) return;
  state.loading = true;
  render();
  try {
    const run = await api(`/api/v1/interviews/${record.id}/parse`, { method: "POST" });
    record.parseRunId = run.parseRunId;
    record.parseEvents = [];
    record.parseError = "";
    record.status = "parsing";
    record.phase = "queued";
    saveRecord(record);
    closeEventSource();
    state.loading = false;
    render();
  } catch (error) {
    state.loading = false;
    setError(readableError(error));
  }
}

function findQuestion(record, id) {
  const topicTurn = (record.topics || []).flatMap(topic => [topic.mainTurn, ...(topic.followUps || [])]).find(item => item.id === id);
  return topicTurn || (record.questions || []).find(item => item.id === id);
}

function syncFlatQuestions(record) {
  record.questions = (record.topics || []).flatMap(topic => [topic.mainTurn, ...(topic.followUps || [])]).sort((a, b) => a.order - b.order);
  record.questionCount = record.topics?.length || 0;
}

function splitFollowUp(questionId) {
  const record = currentRecord();
  const source = record?.topics?.find(topic => topic.followUps.some(item => item.id === questionId));
  if (!source) return;
  const index = source.followUps.findIndex(item => item.id === questionId);
  const [turn] = source.followUps.splice(index, 1);
  turn.turnType = "main";
  turn.parentQuestionId = null;
  turn.topicRootId = turn.id;
  turn.topicTitle = turn.interviewerQuestion.slice(0, 32);
  record.topics.push({ id: turn.id, title: turn.topicTitle, mainTurn: turn, followUps: [] });
  record.topics.sort((a, b) => a.mainTurn.order - b.mainTurn.order);
  state.selectedQuestionId = turn.id;
  syncFlatQuestions(record);
  saveRecord(record);
  toast("追问已拆为独立主题，旧报告将在保存后失效");
  render();
}

function mergeSelectedTopic() {
  const record = currentRecord();
  const source = record?.topics?.find(item => item.id === state.selectedQuestionId);
  const targetId = document.querySelector('input[name="mergeTarget"]:checked')?.value;
  const target = record?.topics?.find(item => item.id === targetId);
  if (!source || !target) return;
  const previousTopics = JSON.parse(JSON.stringify(record.topics));
  const previousSelectedId = source.id;
  const moved = [source.mainTurn, ...source.followUps];
  moved.forEach(turn => {
    turn.turnType = "follow_up";
    turn.parentQuestionId = target.mainTurn.id;
    turn.topicRootId = target.mainTurn.id;
  });
  target.followUps.push(...moved);
  target.followUps.sort((a, b) => a.order - b.order);
  [target.mainTurn, ...target.followUps].forEach(turn => { turn.confirmed = false; });
  target.reviewStatus = "unchecked";
  record.topics = record.topics.filter(item => item.id !== source.id);
  state.selectedQuestionId = target.id;
  record.updatedAt = new Date().toISOString();
  syncFlatQuestions(record);
  saveRecord(record);
  render();
  toast("主题已合并", {
    label: "撤销",
    onClick: () => {
      const latest = currentRecord();
      if (!latest || latest.id !== record.id) return;
      latest.topics = previousTopics;
      state.selectedQuestionId = previousSelectedId;
      latest.updatedAt = new Date().toISOString();
      syncFlatQuestions(latest);
      saveRecord(latest);
      render();
      toast("已撤销主题合并");
    }
  });
}

function seekAudio(value) {
  const audio = document.querySelector("#sourceAudio");
  const seconds = Number(value);
  if (!audio || !Number.isFinite(seconds)) return;
  audio.currentTime = Math.max(0, seconds);
  audio.play().catch(() => {});
}

function bindRunPage() {
  document.querySelector("#resumeRun")?.addEventListener("click", resumeRun);
  document.querySelector("#fallbackRun")?.addEventListener("click", fallbackRun);
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
    if (type === "CHECKPOINT_SAVED") {
      record.runProgress ||= {};
      record.runProgress.completedTopics = Number(item.data.completed || record.runProgress.completedTopics || 0);
    }
    if (type === "AUDIT_COMPLETED") {
      record.runProgress ||= {};
      record.runProgress.auditRound = Number(item.data.round || 0);
    }
    if (type === "TOPIC_REVISION_COMPLETED") {
      record.runProgress ||= {};
      record.runProgress.revisionCount = Number(item.data.revisionCount || 0);
    }
    if (type === "FALLBACK_STARTED") {
      record.agentMode = "deterministic_fallback";
      record.degraded = true;
      record.status = "reviewing";
      record.phase = "fallback";
    }
    if (type === "RUN_FAILED") {
      record.status = "failed";
      record.phase = "failed";
      record.failureCode = item.data.code || "AGENT_FAILED";
      record.runError = item.data.message || "Agent 执行失败";
      closeEventSource();
    }
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

async function fallbackRun() {
  const record = currentRecord();
  if (!record?.runId) return;
  try {
    await api(`/api/v1/runs/${record.runId}/fallback`, { method: "POST" });
    record.status = "reviewing";
    record.phase = "fallback";
    record.agentMode = "deterministic_fallback";
    record.degraded = true;
    record.runError = "";
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
    if (!Array.isArray(rows)) return [];
    const normalized = rows.map(normalizeInterviewRecord);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(normalized));
    return normalized;
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

function toast(message, action = null) {
  clearTimeout(toastTimer);
  const host = document.querySelector("#toastHost");
  if (!host) return;
  host.innerHTML = `<div class="toast"><span>${escapeHtml(message)}</span>${action ? `<button type="button" class="toast-action">${escapeHtml(action.label)}</button>` : ""}</div>`;
  host.querySelector(".toast-action")?.addEventListener("click", () => {
    clearTimeout(toastTimer);
    host.innerHTML = "";
    action.onClick();
  });
  toastTimer = setTimeout(() => { if (host) host.innerHTML = ""; }, action ? 6000 : 2600);
}

function confidenceLabel(value) {
  return { high: "高", medium: "中", low: "低" }[value] || "未知";
}

function formatTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
}

function formatDuration(value) {
  const seconds = Math.max(0, Number(value) || 0);
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function readableError(error) {
  return error instanceof Error ? error.message : String(error || "未知错误");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}
