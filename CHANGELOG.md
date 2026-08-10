# Changelog

本项目使用语义化版本号。重要变更记录在此文件中。

## [Unreleased]

### Added

- Deepgram `nova-3` 音频转写，支持 MP3、M4A、WAV、FLAC 和 OGG。
- ReAct ParseAgent 与 SimpleAgent Worker 组成的分块语义拆题管线。
- 原始片段、人工修订、主题和追问的双轨数据模型与引用定位。
- 异步解析状态、SSE 进度、失败重试和版本化 Agent artifact。
- 三组跨岗位合成面试案例，以及音频、拆题、题型和工作流回归测试。

### Changed

- 新建复盘页升级为粘贴文字、上传文字稿和上传音频三种互斥来源。
- 人工确认页按主题聚合主问题与追问，并支持片段校对和主题调整。
- 报告和面试记录界面优化信息层级、证据呈现、响应式布局和操作入口。
- Windows 启动环境统一使用 UTF-8，避免 Agent 日志中的 Unicode 字符触发 GBK 编码错误。

### Fixed

- 修复日期输入格式、文件选择区域误触、记录列表列错位和操作样式不一致问题。
- 修复低置信度题卡、追问折叠、报告失效和断点恢复相关边界行为。

## [0.1.0] - 2026-08-06

### Added

- FastAPI、SQLite 与 Vanilla JS 组成的面试复盘 MVP。
- ParseAgent、Supervisor、EvidenceAnalyst、QualityAuditor 与 GrowthPlanner 工作流。
- TXT、PDF、DOCX 文档解析与人工题卡确认。
- 五维评分、证据引用、Reflection 审计和七天成长计划。
- SSE 进度事件、会话恢复、Trace 脱敏与 fixture 演示模式。
- Docker、本地启动脚本和基础回归测试。

### Changed

- 优化桌面端字号、流程步骤、文件上传交互和卡片间距。
- 演示数据按钮支持填入与清除两种状态。
