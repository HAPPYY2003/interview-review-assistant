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

export function normalizeInterviewRecord(record = {}) {
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
    questionCount: Number(record.questionCount || 0),
    overallScores: { ...EMPTY_SCORES, ...(record.overallScores || {}) },
    topRisks: Array.isArray(record.topRisks) ? record.topRisks : [],
    auditNotes: Array.isArray(record.auditNotes) ? record.auditNotes : [],
    createdAt: record.createdAt || new Date().toISOString(),
    updatedAt: record.updatedAt || new Date().toISOString()
  };
}

export function normalizeQuestionRecord(record = {}) {
  return {
    ...record,
    id: String(record.id || crypto.randomUUID()),
    order: Number(record.order || 1),
    interviewerQuestion: String(record.interviewerQuestion || ""),
    candidateAnswer: String(record.candidateAnswer || ""),
    questionType: String(record.questionType || "其他"),
    confidence: ["high", "medium", "low"].includes(record.confidence) ? record.confidence : "medium",
    initialDiagnosis: Array.isArray(record.initialDiagnosis) ? record.initialDiagnosis : [],
    scores: record.scores ? { ...EMPTY_SCORES, ...record.scores } : null,
    scoreEvidence: Array.isArray(record.scoreEvidence) ? record.scoreEvidence : [],
    evidenceRefs: Array.isArray(record.evidenceRefs) ? record.evidenceRefs : [],
    strengths: Array.isArray(record.strengths) ? record.strengths : [],
    weaknesses: Array.isArray(record.weaknesses) ? record.weaknesses : [],
    knowledgeToPrepare: Array.isArray(record.knowledgeToPrepare) ? record.knowledgeToPrepare : [],
    roleFitDiagnosis: record.roleFitDiagnosis || {},
    starRewrite: record.starRewrite || {},
    priority: record.priority || { level: "medium", reason: "" }
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
  const lines = [
    `# ${interview.company || "未填写公司"} · ${interview.position || "未填写岗位"} 面试复盘`,
    "",
    `- 轮次：${interview.round || "未填写"}`,
    `- 日期：${interview.interviewDate || "未填写"}`,
    `- 分析模式：${modeLabel(interview.analysisMode)}`,
    "",
    "## 整场结论",
    "",
    interview.summary || "暂无总结",
    "",
    "## 五维评分",
    "",
    ...Object.entries(SCORE_LABELS).map(([key, label]) => `- ${label}：${Number(scores[key] || 0).toFixed(1)}`),
    "",
    "## 行动计划",
    "",
    ...(actions.length ? actions.map(item => `- [${item.completed ? "x" : " "}] **${item.title}**：${item.description || ""}`) : ["- 暂无行动项"]),
    "",
    "## 逐题复盘",
    ""
  ];
  questions.forEach(question => {
    lines.push(
      `### ${question.order}. ${question.interviewerQuestion}`,
      "",
      `综合评分：${Number(question.scores?.overall || 0).toFixed(1)}`,
      "",
      "**原回答**",
      "",
      question.candidateAnswer || "暂无",
      "",
      "**诊断**",
      "",
      question.diagnosis || "暂无",
      "",
      "**STAR 优化回答**",
      "",
      question.starRewrite?.fullAnswer || "暂无",
      ""
    );
  });
  return lines.join("\n");
}

