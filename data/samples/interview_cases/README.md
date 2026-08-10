# Offer Radar 完整检测材料

本目录中的内容全部为虚构数据，不包含真实个人信息，可用于本地功能测试。

每个案例包含：

- `profile.json`：公司、岗位、轮次、日期、复盘目标和建议检查点。
- `job_description.txt`：岗位 JD，可粘贴或作为 TXT 上传。
- `resume.txt`：候选人简历材料，可粘贴或作为 TXT 上传。
- `transcript.txt`：面试文字稿，可粘贴或作为 TXT 上传。

## 案例说明

1. `case-01-product-manager`：回答证据较完整，包含连续追问、量化结果和跨团队决策，适合检查主题聚合与高质量报告。
2. `case-02-data-analyst-asr`：模拟语音转写文本，使用 `speaker_0/speaker_1` 标签，部分回答较模糊，适合检查说话人校对、低置信度确认和证据不足提示。
3. `case-03-backend-engineer`：包含设备噪声、技术追问和前后矛盾，适合检查噪声排除、追问影响标签与 Reflection 审计。

## 使用方式

打开“新建复盘”，根据 `profile.json` 填写基本信息，再分别上传或粘贴 JD、简历和文字稿。所有案例均应选择“粘贴文字”或“上传文字稿”，不需要调用 Deepgram。
