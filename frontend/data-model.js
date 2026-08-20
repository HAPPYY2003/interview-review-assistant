export const EMPTY_SCORES = {
  relevance: 0,
  structure: 0,
  evidence: 0,
  depth: 0,
  roleFit: 0,
  overall: 0
};

export const SCORE_LABELS = {
  relevance: "回答相关性",
  structure: "表达结构",
  evidence: "事实证据",
  depth: "分析深度",
  roleFit: "岗位匹配",
  overall: "综合"
};

export const GAP_CATEGORY_LABELS = {
  hard_skill: "专业技能",
  soft_skill: "软技能",
  domain_knowledge: "业务知识",
  method_tool: "方法与工具",
  case_material: "案例与材料"
};

export const SIGNAL_TYPE_LABELS = {
  request_detail: "要求补充细节",
  verify_contribution: "核查个人贡献",
  verify_data: "核查结果数据",
  check_depth: "检查分析深度",
  challenge_consistency: "质疑真实性或一致性",
  explicit_approval: "明确表达认可",
  possible_topic_end: "可能结束话题",
  unclear: "信号不明确"
};

function performanceLevel(score) {
  if (score >= 8.5) return "表现突出";
  if (score >= 7) return "表现良好";
  if (score >= 6) return "基本合格";
  if (score >= 4) return "需要加强";
  return "准备不足";
}

const QUESTION_TYPE_VALUES = ["自我介绍", "项目经历", "技术知识", "行为面试", "业务理解", "职业规划", "反问环节", "其他"];
const QUESTION_TYPE_ALIASES = {
  self_intro: "自我介绍",
  self_introduction: "自我介绍",
  personal_introduction: "自我介绍",
  project: "项目经历",
  project_experience: "项目经历",
  experience: "项目经历",
  technical: "技术知识",
  technical_knowledge: "技术知识",
  technical_question: "技术知识",
  business: "业务理解",
  business_understanding: "业务理解",
  product_sense: "业务理解",
  career: "职业规划",
  career_planning: "职业规划",
  reverse_question: "反问环节",
  candidate_question: "反问环节",
  other: "其他"
};

function inferQuestionType(text = "") {
  if (/自我介绍|介绍(?:一下|下)?你自己|(?:简单|先)?介绍(?:一下|下)?自己|(?:简单)?说(?:一下|下)?(?:你的)?(?:基本情况|个人背景)|(?:做|作)(?:一下|下|个)?(?:个人|自我)介绍/.test(text)) return "自我介绍";
  if (/有什么想问|还有什么问题|想了解我们|反问/.test(text)) return "反问环节";
  if (/冲突|失败|协作|分歧|压力|说服|推动.*分歧/.test(text)) return "行为面试";
  if (/指标|数据|算法|实验|测试|技术|架构|系统设计|性能|代码/.test(text)) return "技术知识";
  if (/职业规划|职业发展|离职|未来(?:三年|五年|职业)|为什么选择(?:我们|这家公司|该公司|这个岗位|该岗位|这个行业)/.test(text)) return "职业规划";
  if (/项目|经历|负责|挑战|成果/.test(text)) return "项目经历";
  if (/业务|用户|市场|产品|需求|商业/.test(text)) return "业务理解";
  return "其他";
}

function normalizeQuestionType(value, question = "", topicTitle = "") {
  const raw = String(value || "").trim();
  if (QUESTION_TYPE_VALUES.includes(raw)) return raw;
  const topicPrefix = String(topicTitle || "").split(/[：:]/, 1)[0].trim();
  if (QUESTION_TYPE_VALUES.includes(topicPrefix)) return topicPrefix;
  const key = raw.toLowerCase().replace(/[\s-]+/g, "_");
  return QUESTION_TYPE_ALIASES[key] || inferQuestionType(`${topicTitle} ${question}`);
}

function inferTopicTitle(question = "", questionType = "") {
  const kind = normalizeQuestionType(questionType, question);
  if (kind === "自我介绍") return "个人背景与岗位契合";
  if (kind === "行为面试") {
    if (/(分歧|冲突)/.test(question) && /(跨团队|研发|设计|协作|团队)/.test(question)) return "跨团队分歧处理";
    if (/(分歧|冲突)/.test(question)) return "分歧与冲突处理";
    if (/(失败|失误|挫折)/.test(question)) return "失败复盘与改进";
    if (/(压力|紧急|截止)/.test(question)) return "压力与优先级管理";
    if (/(说服|影响|推动|协作)/.test(question)) return "沟通协作与推动";
    return "行为经历复盘";
  }
  if (kind === "技术知识") {
    if (/(实验|显著|样本量|分流)/.test(question)) return "实验分析与决策";
    if (/(数据|指标|漏斗)/.test(question)) return "数据分析与指标";
    if (/(架构|系统设计|性能)/.test(question)) return "系统设计与架构";
    if (/(算法|模型|代码)/.test(question)) return "算法与技术原理";
    return "技术能力";
  }
  if (kind === "项目经历") {
    if (/(挑战|困难|复杂)/.test(question)) return "挑战项目复盘";
    if (/(成果|结果|提升|增长)/.test(question)) return "项目成果与影响";
    return "项目职责与实践";
  }
  if (kind === "业务理解") {
    if (/(用户|需求)/.test(question)) return "用户与需求洞察";
    if (/(市场|竞品|商业)/.test(question)) return "市场与商业判断";
    return "业务与产品理解";
  }
  if (kind === "职业规划") return "职业选择与规划";
  if (kind === "反问环节") return "候选人反问";
  return String(question || "").replace(/[？?。！!]/g, "").trim().slice(0, 18) || "其他问题";
}

export function countQuestionTurns(record = {}) {
  const topics = Array.isArray(record.topics) ? record.topics : [];
  if (topics.length) {
    return topics.reduce((total, topic) => {
      const mainCount = topic?.mainTurn ? 1 : 0;
      const followUpCount = Array.isArray(topic?.followUps) ? topic.followUps.length : 0;
      return total + mainCount + followUpCount;
    }, 0);
  }

  const questions = Array.isArray(record.questions) ? record.questions : [];
  if (questions.length) {
    const isFlatTurnList = questions.some(question => question?.turnType === "follow_up");
    if (isFlatTurnList) return questions.length;
    return questions.reduce((total, question) => {
      const followUpCount = Array.isArray(question?.followUpTurns) ? question.followUpTurns.length : 0;
      return total + 1 + followUpCount;
    }, 0);
  }

  return Number(record.questionCount || 0);
}

export function normalizeInterviewRecord(record = {}) {
  const questions = Array.isArray(record.questions) ? record.questions.map(normalizeQuestionRecord) : [];
  const topics = Array.isArray(record.topics)
    ? record.topics.map(topic => {
        const mainTurn = normalizeQuestionRecord(topic.mainTurn || {});
        const sourceTitle = String(topic.title || mainTurn.topicTitle || "").trim();
        const title = !sourceTitle || (QUESTION_TYPE_VALUES.includes(sourceTitle) && sourceTitle !== mainTurn.questionType)
          ? inferTopicTitle(mainTurn.interviewerQuestion, mainTurn.questionType)
          : sourceTitle;
        mainTurn.topicTitle = title;
        return {
          ...topic,
          id: String(topic.id || mainTurn.id || crypto.randomUUID()),
          title,
          mainTurn,
          followUps: Array.isArray(topic.followUps) ? topic.followUps.map(normalizeQuestionRecord) : []
        };
      })
    : [];
  return {
    ...record,
    id: String(record.id || crypto.randomUUID()),
    company: String(record.company || ""),
    position: String(record.position || ""),
    round: String(record.round || ""),
    interviewDate: String(record.interviewDate || record.interview_date || ""),
    reviewGoal: String(record.reviewGoal || record.review_goal || ""),
    analysisMode: record.analysisMode || record.analysis_mode || "full_context",
    status: String(record.status || "draft").toLowerCase(),
    questionCount: countQuestionTurns({ ...record, questions, topics }),
    questions,
    topics,
    segments: Array.isArray(record.segments) ? record.segments : [],
    atoms: Array.isArray(record.atoms) ? record.atoms : [],
    parseEvents: Array.isArray(record.parseEvents) ? record.parseEvents : [],
    unresolvedCount: Number(record.unresolvedCount || 0),
    audio: record.audio || null,
    overallScores: { ...EMPTY_SCORES, ...(record.overallScores || {}) },
    topRisks: Array.isArray(record.topRisks) ? record.topRisks : [],
    auditNotes: Array.isArray(record.auditNotes) ? record.auditNotes : [],
    growthPlanAudit: record.growthPlanAudit && typeof record.growthPlanAudit === "object" ? record.growthPlanAudit : null,
    report: record.report ? normalizeReportRecord(record.report) : null,
    createdAt: record.createdAt || new Date().toISOString(),
    updatedAt: record.updatedAt || new Date().toISOString()
  };
}

export function normalizeQuestionRecord(record = {}, options = {}) {
  const context = options && typeof options === "object" && !Array.isArray(options) ? options : {};
  const reportSchemaVersion = Number(context.reportSchemaVersion || 0);
  const compatibilityLabel = reportSchemaVersion > 0 && reportSchemaVersion < 2 ? "此旧版报告" : "当前报告";
  const extractedQuestion = String(record.extractedQuestion || record.extracted_question || record.interviewerQuestion || record.interviewer_question || record.question || "");
  const extractedAnswer = String(record.extractedAnswer || record.extracted_answer || record.candidateAnswer || record.candidate_answer || record.answer || "");
  const editedQuestion = String(record.editedQuestion || record.edited_question || "");
  const editedAnswer = String(record.editedAnswer || record.edited_answer || "");
  const topicTitle = String(record.topicTitle || record.topic_title || "");
  const followUpTurns = Array.isArray(record.followUpTurns) ? record.followUpTurns.map(turn => normalizeQuestionRecord(turn, context)) : [];
  const strengthClaims = Array.isArray(record.strengthClaims)
    ? record.strengthClaims
    : (Array.isArray(record.strengths) ? record.strengths.map(text => ({ text, evidenceIds: [] })) : []);
  const weaknessClaims = Array.isArray(record.weaknessClaims)
    ? record.weaknessClaims
    : (Array.isArray(record.weaknesses) ? record.weaknesses.map(text => ({ text, evidenceIds: [] })) : []);
  const star = record.starRewrite || {};
  const legacySections = [
    ["S", "情境", star.situation], ["T", "任务", star.task],
    ["A", "行动", star.action], ["R", "结果", star.result]
  ].filter(([, , value]) => value).map(([key, label, value]) => ({
    key, label, guidance: `补充${label}信息。`, draft: value, evidenceIds: star.evidenceIds || []
  }));
  const answerLogic = record.answerLogic || {
    summary: `${compatibilityLabel}未生成结构化回答路径。`,
    steps: [],
    gaps: weaknessClaims
  };
  const interviewerSignals = Array.isArray(record.interviewerSignals)
    ? record.interviewerSignals
    : followUpTurns.filter(turn => turn.followUpImpact).map(turn => ({
        turnId: turn.id,
        type: { "补充有效证据": "request_detail", "暴露回答不足": "check_depth", "存在前后矛盾": "challenge_consistency" }[turn.followUpImpact] || "unclear",
        interpretation: turn.followUpRationale || `${compatibilityLabel}仅记录追问影响：${turn.followUpImpact}`,
        confidence: "low",
        evidenceIds: Array.isArray(turn.evidenceIds) ? turn.evidenceIds : []
      }));
  const recommendedAnswer = record.recommendedAnswer || {
    framework: {
      type: "STAR", name: "STAR",
      reason: record.suggestedStructure || `${compatibilityLabel}仅保留了 STAR 优化结果。`,
      sections: legacySections
    },
    fullAnswer: star.fullAnswer || record.improvedAnswer || "",
    evidenceIds: Array.isArray(star.evidenceIds) ? star.evidenceIds : [],
    missingInformation: Array.isArray(star.missingInformation) ? star.missingInformation : []
  };
  return {
    ...record,
    id: String(record.id || crypto.randomUUID()),
    order: Number(record.order || 1),
    interviewerQuestion: String(record.interviewerQuestion || editedQuestion || extractedQuestion),
    candidateAnswer: String(record.candidateAnswer || editedAnswer || extractedAnswer),
    questionType: normalizeQuestionType(record.questionType || record.question_type, extractedQuestion, topicTitle),
    confidence: ["high", "medium", "low"].includes(record.confidence) ? record.confidence : "medium",
    initialDiagnosis: Array.isArray(record.initialDiagnosis) ? record.initialDiagnosis : [],
    scores: record.scores ? { ...EMPTY_SCORES, ...record.scores } : null,
    scoreEvidence: Array.isArray(record.scoreEvidence) ? record.scoreEvidence : [],
    evidenceRefs: Array.isArray(record.evidenceRefs) ? record.evidenceRefs : [],
    strengths: Array.isArray(record.strengths) ? record.strengths : [],
    weaknesses: Array.isArray(record.weaknesses) ? record.weaknesses : [],
    strengthClaims,
    weaknessClaims,
    answerLogic,
    interviewerSignals,
    recommendedAnswer,
    knowledgeToPrepare: Array.isArray(record.knowledgeToPrepare) ? record.knowledgeToPrepare : [],
    roleFitDiagnosis: record.roleFitDiagnosis || {},
    starRewrite: record.starRewrite || {},
    priority: record.priority || { level: "medium", reason: "" },
    topicRootId: String(record.topicRootId || record.topic_root_id || record.id || ""),
    parentQuestionId: record.parentQuestionId || record.parent_question_id || null,
    turnType: (record.turnType || record.turn_type) === "follow_up" ? "follow_up" : "main",
    extractedQuestion,
    extractedAnswer,
    editedQuestion,
    editedAnswer,
    topicTitle,
    needsConfirmation: Boolean(record.needsConfirmation ?? record.needs_confirmation),
    provenanceStatus: record.provenanceStatus || record.provenance_status || (editedQuestion || editedAnswer ? "edited" : "source"),
    confidenceScore: Number(record.confidenceScore ?? ({ high: 90, medium: 75, low: 50 }[record.confidence] || 75)),
    rawConfidenceScore: Number(record.rawConfidenceScore ?? record.confidenceScore ?? ({ high: 90, medium: 75, low: 50 }[record.confidence] || 75)),
    confidenceDetails: record.confidenceDetails && typeof record.confidenceDetails === "object" ? record.confidenceDetails : {},
    confirmationReasons: Array.isArray(record.confirmationReasons) ? record.confirmationReasons : [],
    parseMethod: String(record.parseMethod || "legacy"),
    followUpImpact: String(record.followUpImpact || ""),
    questionSegmentIds: Array.isArray(record.questionSegmentIds) ? record.questionSegmentIds : (Array.isArray(record.question_segment_ids) ? record.question_segment_ids : []),
    answerSegmentIds: Array.isArray(record.answerSegmentIds) ? record.answerSegmentIds : (Array.isArray(record.answer_segment_ids) ? record.answer_segment_ids : []),
    followUpTurns
  };
}

export function normalizeReportRecord(report = {}) {
  const reportSchemaVersion = Number(report.reportSchemaVersion || 1);
  const compatibilityLabel = reportSchemaVersion < 2 ? "此旧版报告" : "当前报告";
  const sourceInterview = report.interview || {};
  const scores = { ...EMPTY_SCORES, ...(sourceInterview.overallScores || {}) };
  const sourceEvaluation = sourceInterview.overallEvaluation || {};
  const overallEvaluation = {
    summary: sourceEvaluation.summary || sourceInterview.summary || "暂无总结",
    strengths: Array.isArray(sourceEvaluation.strengths) ? sourceEvaluation.strengths : [],
    risks: Array.isArray(sourceEvaluation.risks) ? sourceEvaluation.risks : [],
    nextFocus: sourceEvaluation.nextFocus || sourceInterview.nextFocus || "",
    score: Number(sourceEvaluation.score ?? scores.overall ?? 0),
    performanceLevel: sourceEvaluation.performanceLevel || performanceLevel(Number(scores.overall || 0))
  };
  const sourceGaps = Array.isArray(sourceInterview.capabilityGaps) && sourceInterview.capabilityGaps.length
    ? sourceInterview.capabilityGaps
    : (sourceInterview.topRisks || []).map((risk, index) => ({
        id: `legacy-gap-${index + 1}`,
        category: "case_material",
        title: risk.title || "待补充能力缺口",
        description: risk.reason || `${compatibilityLabel}未生成结构化缺口。`,
        impact: `${compatibilityLabel}未记录具体影响。`,
        priority: risk.severity === "high" ? "high" : "medium",
        topicIds: risk.topicIds || (risk.questionId ? [risk.questionId] : []),
        evidenceIds: [], learningItems: [], preparationItems: [], legacy: true
      }));
  const gaps = sourceGaps.map((gap, index) => ({
    id: String(gap.id || `gap-${index + 1}`),
    category: gap.category || "case_material",
    title: String(gap.title || "待补充能力缺口"),
    description: String(gap.description || ""),
    impact: String(gap.impact || ""),
    priority: gap.priority === "high" ? "high" : "medium",
    topicIds: Array.isArray(gap.topicIds) ? gap.topicIds : [],
    evidenceIds: Array.isArray(gap.evidenceIds) ? gap.evidenceIds : [],
    learningItems: Array.isArray(gap.learningItems) ? gap.learningItems : [],
    preparationItems: Array.isArray(gap.preparationItems) ? gap.preparationItems : [],
    legacy: Boolean(gap.legacy)
  }));
  const fallbackGapId = gaps[0]?.id || "legacy-gap-1";
  const actions = (report.actions || []).map((action, index) => {
    const status = ["pending", "in_progress", "completed", "skipped"].includes(action.status)
      ? action.status
      : (action.completed ? "completed" : "pending");
    return {
      ...action,
      id: String(action.id || `action-${index + 1}`),
      order: Number(action.order || action.day || index + 1),
      type: action.type === "learning" ? "learning" : "preparation",
      gapIds: Array.isArray(action.gapIds) && action.gapIds.length ? action.gapIds : [fallbackGapId],
      successCriterion: action.successCriterion || "完成任务并保留练习记录。",
      status,
      completed: status === "completed",
      startedAt: action.startedAt || null,
      completedAt: action.completedAt || null,
      userNote: String(action.userNote || ""),
      completionEvidence: String(action.completionEvidence || ""),
      selfRating: action.selfRating === null || action.selfRating === undefined || action.selfRating === ""
        ? null
        : (Number.isFinite(Number(action.selfRating)) ? Number(action.selfRating) : null)
    };
  });
  return {
    ...report,
    reportSchemaVersion,
    isLegacyReport: reportSchemaVersion < 2,
    interview: {
      ...sourceInterview,
      overallScores: scores,
      overallEvaluation,
      capabilityGaps: gaps,
      growthPlanAudit: sourceInterview.growthPlanAudit && typeof sourceInterview.growthPlanAudit === "object"
        ? sourceInterview.growthPlanAudit
        : null
    },
    questions: (report.questions || []).map(item => normalizeQuestionRecord(item, { reportSchemaVersion })),
    actions
  };
}

export function modeLabel(mode) {
  return {
    full_context: "JD + 简历完整上下文",
    job_context: "岗位上下文",
    general: "通用复盘"
  }[mode] || "通用复盘";
}

export function buildMarkdownReport(interview, questions, actions) {
  const scores = interview.overallScores || EMPTY_SCORES;
  const evaluation = interview.overallEvaluation || {};
  const gaps = interview.capabilityGaps || [];
  const lines = [
    `# ${interview.company || "未填写公司"} · ${interview.position || "未填写岗位"} 面试复盘`,
    "",
    `- 轮次：${interview.round || "未填写"}`,
    `- 日期：${interview.interviewDate || "未填写"}`,
    `- 分析模式：${modeLabel(interview.analysisMode)}`,
    "",
    "## 面试综合评价",
    "",
    `- 面试表现：${Number(evaluation.score ?? scores.overall ?? 0).toFixed(1)}/10 · ${evaluation.performanceLevel || performanceLevel(Number(scores.overall || 0))}`,
    evaluation.summary || interview.summary || "暂无总结",
    "",
    `- 主要优势：${(evaluation.strengths || []).map(item => item.text || item).join("；") || "暂无"}`,
    `- 主要风险：${(evaluation.risks || []).map(item => item.text || item).join("；") || "暂无"}`,
    `- 下一场重点：${evaluation.nextFocus || interview.nextFocus || "暂无"}`,
    "",
    "## 五维评分",
    "",
    ...Object.entries(SCORE_LABELS).map(([key, label]) => `- ${label}：${Number(scores[key] || 0).toFixed(1)}`),
    "",
    "## 技能 / 知识缺口",
    "",
    ...(gaps.length ? gaps.map((gap, index) => `${index + 1}. **${gap.title}**（${GAP_CATEGORY_LABELS[gap.category] || gap.category}，${gap.priority === "high" ? "高优先级" : "中优先级"}）\n   - 缺口：${gap.description}\n   - 影响：${gap.impact}\n   - 学习项：${(gap.learningItems || []).join("；") || "暂无"}\n   - 准备项：${(gap.preparationItems || []).join("；") || "暂无"}`) : ["- 暂无可靠缺口"]),
    "",
    "## 下一步行动计划",
    "",
    ...(actions.length ? actions.map((item, index) => `- [${item.completed ? "x" : " "}] **行动 ${item.order || item.day || index + 1} · ${item.type === "learning" ? "学习项" : "准备项"} · ${item.title}**\n  - 任务：${item.description || ""}\n  - 提升维度：${SCORE_LABELS[item.dimension] || item.dimension || "综合训练"}\n  - 完成标准：${item.successCriterion || "暂无"}`) : ["- 暂无行动项"]),
    "",
    "## 质量审计",
    "",
    `- 逐题复盘审计：${(interview.auditNotes || [])[0] || "暂无审计摘要"}`,
    interview.growthPlanAudit
      ? `- 成长计划终审：${interview.growthPlanAudit.summary || "已完成"}（第 ${interview.growthPlanAudit.round || 1} 轮，修订 ${interview.growthPlanAudit.revisionCount || 0} 次）`
      : "- 成长计划终审：该报告生成时未启用成长计划终审",
    ...((interview.growthPlanAudit?.findings || []).map(item => `  - [${item.severity}] ${item.message}`)),
    "",
    "## 逐题深度复盘",
    ""
  ];
  questions.forEach(question => {
    lines.push(
      `### ${question.order}. ${question.interviewerQuestion}`,
      "",
      `综合评分：${Number(question.scores?.overall || 0).toFixed(1)}`,
      "",
      "**回答逻辑**",
      "",
      question.answerLogic?.summary || "此报告未提供结构化回答路径。",
      ...(question.answerLogic?.steps || []).map(item => `${item.order}. **${item.label}**：${item.content}`),
      "",
      "**面试官信号**",
      "",
      ...((question.interviewerSignals || []).length ? question.interviewerSignals.map(item => `- ${SIGNAL_TYPE_LABELS[item.type] || item.type}：${item.interpretation}`) : ["- 未发现明确面试官信号"]),
      "",
      "**问题诊断**",
      "",
      question.diagnosis || "暂无",
      "",
      "**回答改进**",
      "",
      `推荐框架：${question.recommendedAnswer?.framework?.name || "暂无"}`,
      question.recommendedAnswer?.framework?.reason || "",
      "",
      question.recommendedAnswer?.fullAnswer || "暂无",
      "",
      "**证据引用**",
      "",
      ...((question.evidenceRefs || []).length ? question.evidenceRefs.map((item, index) => `- E${index + 1} ${item.quote || "暂无引用"}（${item.locator || "未记录位置"}）`) : ["- 暂无证据"]),
      ""
    );
  });
  return lines.join("\n");
}
