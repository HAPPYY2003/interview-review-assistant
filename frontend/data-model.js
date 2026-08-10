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

const QUESTION_TYPE_VALUES = ["项目经历", "技术知识", "行为面试", "业务理解", "职业规划", "反问环节", "其他"];
const QUESTION_TYPE_ALIASES = {
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
    questionCount: Number(record.questionCount || questions.length),
    questions,
    topics,
    segments: Array.isArray(record.segments) ? record.segments : [],
    parseEvents: Array.isArray(record.parseEvents) ? record.parseEvents : [],
    unresolvedCount: Number(record.unresolvedCount || 0),
    audio: record.audio || null,
    overallScores: { ...EMPTY_SCORES, ...(record.overallScores || {}) },
    topRisks: Array.isArray(record.topRisks) ? record.topRisks : [],
    auditNotes: Array.isArray(record.auditNotes) ? record.auditNotes : [],
    createdAt: record.createdAt || new Date().toISOString(),
    updatedAt: record.updatedAt || new Date().toISOString()
  };
}

export function normalizeQuestionRecord(record = {}) {
  const extractedQuestion = String(record.extractedQuestion || record.interviewerQuestion || "");
  const extractedAnswer = String(record.extractedAnswer || record.candidateAnswer || "");
  const editedQuestion = String(record.editedQuestion || "");
  const editedAnswer = String(record.editedAnswer || "");
  const topicTitle = String(record.topicTitle || record.topic_title || "");
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
    knowledgeToPrepare: Array.isArray(record.knowledgeToPrepare) ? record.knowledgeToPrepare : [],
    roleFitDiagnosis: record.roleFitDiagnosis || {},
    starRewrite: record.starRewrite || {},
    priority: record.priority || { level: "medium", reason: "" },
    topicRootId: String(record.topicRootId || record.id || ""),
    parentQuestionId: record.parentQuestionId || null,
    turnType: record.turnType === "follow_up" ? "follow_up" : "main",
    extractedQuestion,
    extractedAnswer,
    editedQuestion,
    editedAnswer,
    topicTitle,
    needsConfirmation: Boolean(record.needsConfirmation),
    provenanceStatus: record.provenanceStatus || (editedQuestion || editedAnswer ? "edited" : "source"),
    followUpImpact: String(record.followUpImpact || ""),
    questionSegmentIds: Array.isArray(record.questionSegmentIds) ? record.questionSegmentIds : [],
    answerSegmentIds: Array.isArray(record.answerSegmentIds) ? record.answerSegmentIds : [],
    followUpTurns: Array.isArray(record.followUpTurns) ? record.followUpTurns.map(normalizeQuestionRecord) : []
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
