# 版本管理说明

## 分支约定

- `main`：可运行、可演示的稳定版本。
- `codex/<topic>`：Codex 协作开发分支，例如 `codex/evidence-drawer`。
- `fix/<topic>`：人工维护的缺陷修复分支。
- `docs/<topic>`：只修改文档的分支。

功能开发完成并通过测试后再合并到 `main`。不要直接提交 `.env`、本地数据库、上传材料、会话、Trace 或日志。

## 提交约定

提交信息采用简洁的 Conventional Commits 风格：

- `feat:` 新功能
- `fix:` 缺陷修复
- `refactor:` 不改变外部行为的重构
- `test:` 测试变更
- `docs:` 文档变更
- `chore:` 工程、依赖或仓库维护

示例：`feat: add evidence reference drawer`

## 版本约定

版本号采用 `MAJOR.MINOR.PATCH`：

- `MAJOR`：不兼容的架构或接口升级。
- `MINOR`：向后兼容的新能力。
- `PATCH`：向后兼容的修复。

每次发布前更新 `CHANGELOG.md`，在 `main` 上创建带说明的标签，例如：

```powershell
git tag -a v0.2.0 -m "Offer Radar Agent v0.2.0"
git push origin main --follow-tags
```

## 发布检查

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check frontend\app.js
node --check frontend\data-model.js
git status --short
```

确认测试通过、工作区内容符合预期，并检查暂存区中没有密钥和私人面试材料后再推送。
