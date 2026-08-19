from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUTPUT_DIR = Path(sys.argv[2] if len(sys.argv) > 2 else "data")
BROWSER_PATH = os.getenv(
    "PLAYWRIGHT_BROWSER_PATH",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)


def assert_no_overflow(page: Page, label: str) -> None:
    dimensions = page.evaluate(
        "() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth })"
    )
    if dimensions["scrollWidth"] > dimensions["width"] + 1:
        raise AssertionError(
            f"{label} has horizontal overflow: {dimensions['scrollWidth']} > {dimensions['width']}"
        )


def run() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=BROWSER_PATH)
        try:
            desktop = browser.new_context(viewport={"width": 1440, "height": 900})
            page = desktop.new_page()
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.goto(f"{BASE_URL}/#/new", wait_until="networkidle")
            page.evaluate("localStorage.clear()")
            page.reload(wait_until="networkidle")
            assert "HelloAgents" not in page.locator(".sidebar").inner_text()

            step_tops = page.locator(".step").evaluate_all(
                "nodes => nodes.map(node => Math.round(node.getBoundingClientRect().top))"
            )
            assert len(set(step_tops)) == 1, f"desktop steps wrapped: {step_tops}"
            step_alignment = page.evaluate(
                """() => {
                    const pageBox = document.querySelector('.page').getBoundingClientRect();
                    const stepsBox = document.querySelector('.steps').getBoundingClientRect();
                    const stepBoxes = [...document.querySelectorAll('.step')].map(
                        step => step.getBoundingClientRect()
                    );
                    return {
                        leftDelta: Math.abs(pageBox.left - stepsBox.left),
                        rightDelta: Math.abs(pageBox.right - stepsBox.right),
                        firstStepDelta: Math.abs(pageBox.left - stepBoxes[0].left),
                        lastStepDelta: Math.abs(pageBox.right - stepBoxes.at(-1).right),
                    };
                }"""
            )
            assert step_alignment["leftDelta"] <= 1, step_alignment
            assert step_alignment["rightDelta"] <= 1, step_alignment
            assert step_alignment["firstStepDelta"] <= 1, step_alignment
            assert step_alignment["lastStepDelta"] <= 1, step_alignment
            date_input = page.locator('input[name="interviewDate"]')
            assert date_input.get_attribute("placeholder") == "YYYY-MM-DD"
            assert date_input.get_attribute("type") == "text"

            flat_questions = [
                {
                    "id": f"flat-turn-{index}",
                    "turnType": "follow_up" if index >= 8 else "main",
                    "interviewerQuestion": f"平铺问题 {index + 1}",
                }
                for index in range(20)
            ]
            nested_questions = [
                {
                    "id": f"nested-main-{index}",
                    "turnType": "main",
                    "interviewerQuestion": f"主题问题 {index + 1}",
                    "followUpTurns": [
                        {
                            "id": f"nested-follow-{index}-{follow_index}",
                            "turnType": "follow_up",
                            "interviewerQuestion": f"追问 {index + 1}-{follow_index + 1}",
                        }
                        for follow_index in range(3 if index < 4 else 0)
                    ],
                }
                for index in range(8)
            ]
            page.evaluate(
                "rows => localStorage.setItem('offer-radar-agent-v1', JSON.stringify(rows))",
                [
                    {
                        "id": "count-flat",
                        "company": "平铺题目",
                        "position": "测试岗位",
                        "status": "waiting_confirmation",
                        "questions": flat_questions,
                        "updatedAt": "2026-08-19T12:00:00+08:00",
                    },
                    {
                        "id": "count-nested",
                        "company": "嵌套题目",
                        "position": "测试岗位",
                        "status": "completed",
                        "questions": nested_questions,
                        "questionCount": 8,
                        "updatedAt": "2026-08-19T11:00:00+08:00",
                    },
                ],
            )
            page.goto(f"{BASE_URL}/#/", wait_until="networkidle")
            page.reload(wait_until="networkidle")
            question_counts = page.locator('.table-row.data [data-label="问题"]').all_inner_texts()
            assert question_counts == ["20 道", "20 道"], question_counts
            self_intro_question = {
                "id": "self-intro-question",
                "turnType": "main",
                "topicRootId": "self-intro-question",
                "interviewerQuestion": "请用三分钟做一个自我介绍。",
                "candidateAnswer": "我有五年企业产品经验。",
                "questionType": "自我介绍",
                "topicTitle": "个人背景与岗位契合",
                "confidence": "high",
            }
            page.evaluate(
                "row => localStorage.setItem('offer-radar-agent-v1', JSON.stringify([row]))",
                {
                    "id": "self-intro-ui",
                    "company": "题型测试",
                    "position": "产品经理",
                    "status": "waiting_confirmation",
                    "questions": [self_intro_question],
                    "topics": [
                        {
                            "id": "self-intro-question",
                            "title": "个人背景与岗位契合",
                            "mainTurn": self_intro_question,
                            "followUps": [],
                        }
                    ],
                    "updatedAt": "2026-08-19T12:30:00+08:00",
                },
            )
            page.goto(f"{BASE_URL}/#/parse/self-intro-ui", wait_until="networkidle")
            page.reload(wait_until="networkidle")
            question_type_select = page.locator("[data-topic-type]")
            assert question_type_select.input_value() == "自我介绍"
            assert "自我介绍" in question_type_select.locator("option").all_inner_texts()
            if os.getenv("UI_QUESTION_COUNT_ONLY") == "1":
                print(json.dumps({"ok": True, "questionCounts": question_counts}, ensure_ascii=False))
                return
            page.evaluate("localStorage.clear()")
            page.goto(f"{BASE_URL}/#/new", wait_until="networkidle")
            page.reload(wait_until="networkidle")

            page.get_by_role("button", name="上传文字稿").click()
            picker_hit = page.locator('[data-source-panel="text"] .file-row').evaluate(
                """row => {
                    const box = row.getBoundingClientRect();
                    const target = document.elementFromPoint(box.left + 16, box.top + box.height / 2);
                    return Boolean(target?.closest('label.file-button'));
                }"""
            )
            assert not picker_hit, "the whole upload row triggers the native file picker"

            page.get_by_role("button", name="粘贴文字").click()
            page.get_by_role("button", name="填入演示数据").click()
            page.get_by_role("checkbox", name=re.compile("全部材料已做必要脱敏")).check()
            page.screenshot(path=OUTPUT_DIR / "ui-new-desktop.png", full_page=True)
            page.get_by_role("button", name="开始解析").click()
            page.wait_for_url(re.compile(r"#/parse/"), timeout=10_000)
            page.locator(".topic-editor").wait_for(timeout=20_000)

            heading_metrics = page.evaluate(
                """() => {
                    const heading = document.querySelector('.topic-editor-heading');
                    const title = document.querySelector('[data-topic-title]').getBoundingClientRect();
                    const select = document.querySelector('[data-topic-type]').getBoundingClientRect();
                    const state = document.querySelector('.topic-state.prominent').getBoundingClientRect();
                    const titleStyle = getComputedStyle(document.querySelector('[data-topic-title]'));
                    const stateStyle = getComputedStyle(document.querySelector('.topic-state.prominent'));
                    const merge = document.querySelector('#openMergeTopic')?.getBoundingClientRect();
                    return {
                        paddingTop: getComputedStyle(heading).paddingTop,
                        typeDisplay: getComputedStyle(document.querySelector('.topic-type-control')).display,
                        titleFontSize: titleStyle.fontSize,
                        titleWidth: title.width,
                        titleLabelFontSize: getComputedStyle(document.querySelector('.topic-title-label')).fontSize,
                        titleBottom: title.bottom,
                        selectBottom: select.bottom,
                        selectWidth: select.width,
                        selectHeight: select.height,
                        stateHeight: state.height,
                        stateCenter: state.top + state.height / 2,
                        titleCenter: title.top + title.height / 2,
                        stateGap: state.left - title.right,
                        stateInsideTitle: document.querySelector('.topic-title-row').contains(document.querySelector('.topic-state.prominent')),
                        statePaddingLeft: stateStyle.paddingLeft,
                        stateFontSize: stateStyle.fontSize,
                        stateFontWeight: stateStyle.fontWeight,
                        stateBackground: stateStyle.backgroundColor,
                        stateBorderColor: stateStyle.borderTopColor,
                        mergeHeight: merge?.height || 0,
                    };
                }"""
            )
            assert heading_metrics["paddingTop"] == "17px"
            assert heading_metrics["typeDisplay"] == "flex"
            assert abs(heading_metrics["titleBottom"] - heading_metrics["selectBottom"]) <= 1
            assert heading_metrics["titleFontSize"] == "24px"
            assert heading_metrics["titleWidth"] >= 48, "dynamic topic title should remain visible"
            assert heading_metrics["titleLabelFontSize"] == "12px"
            assert 148 <= heading_metrics["selectWidth"] <= 152
            assert 33 <= heading_metrics["selectHeight"] <= 35
            assert heading_metrics["stateHeight"] <= 24
            assert abs(heading_metrics["stateCenter"] - heading_metrics["titleCenter"]) <= 1
            assert 9 <= heading_metrics["stateGap"] <= 11
            assert heading_metrics["stateInsideTitle"]
            assert heading_metrics["statePaddingLeft"] == "7px"
            assert heading_metrics["stateFontSize"] == "12px"
            assert heading_metrics["stateFontWeight"] == "600"
            assert heading_metrics["stateBackground"] == "rgb(241, 245, 249)"
            assert heading_metrics["stateBorderColor"] == "rgb(203, 213, 225)"

            review_header_metrics = page.evaluate(
                """() => {
                    const copy = document.querySelector('.review-header-copy').getBoundingClientRect();
                    const status = document.querySelector('.review-header-status').getBoundingClientRect();
                    const progress = document.querySelector('.review-header-progress').getBoundingClientRect();
                    const button = document.querySelector('#openReviewDialog').getBoundingClientRect();
                    return {
                        eyebrowCount: document.querySelectorAll('.review-header .eyebrow').length,
                        copyCenter: copy.top + copy.height / 2,
                        statusCenter: status.top + status.height / 2,
                        progressButtonGap: button.left - progress.right,
                    };
                }"""
            )
            assert review_header_metrics["eyebrowCount"] == 0
            assert abs(review_header_metrics["copyCenter"] - review_header_metrics["statusCenter"]) <= 1
            assert 11 <= review_header_metrics["progressButtonGap"] <= 13

            main_edit = page.locator(".main-turn-edit")
            assert main_edit.count() == 1, "main turn should expose one edit entry"
            assert main_edit.get_attribute("aria-label") == "编辑主问题和回答"
            assert main_edit.get_attribute("data-tooltip") == "编辑主问题和回答"
            assert page.locator(".review-text-block").count() == 0, "question and answer should use a continuous reading layout"
            assert page.locator(".main-answer-label").inner_text() == "候选人回答"
            main_reading_metrics = page.evaluate(
                """() => ({
                    questionFontSize: getComputedStyle(document.querySelector('.main-question-text')).fontSize,
                    questionWidth: document.querySelector('.main-question-text').getBoundingClientRect().width,
                    answerFontSize: getComputedStyle(document.querySelector('.main-answer p')).fontSize,
                    answerLineHeight: getComputedStyle(document.querySelector('.main-answer p')).lineHeight,
                    answerWidth: document.querySelector('.main-answer p').getBoundingClientRect().width,
                })"""
            )
            assert main_reading_metrics["questionFontSize"] == "17px"
            assert main_reading_metrics["questionWidth"] <= 900
            assert main_reading_metrics["answerFontSize"] == "15px"
            assert float(main_reading_metrics["answerLineHeight"].removesuffix("px")) >= 25
            assert main_reading_metrics["answerWidth"] <= 900
            main_edit.click()
            assert page.locator('.turn-edit-form [data-edit-field="question"]').count() == 1
            assert page.locator('.turn-edit-form [data-edit-field="answer"]').count() == 1
            page.locator("[data-cancel-edit]").click()

            follow_ups = page.locator(".follow-up-question")
            if follow_ups.count():
                assert all(follow_ups.evaluate_all("nodes => nodes.map(node => Boolean(node.querySelector('.topic-state') && node.querySelector('.follow-up-question-meta .lucide-icon')))"))
                assert all(value == "false" for value in follow_ups.evaluate_all("nodes => nodes.map(node => node.getAttribute('aria-expanded'))"))
                assert page.locator(".follow-up-answer").count() == 0
                follow_ups.first.click()
                assert page.locator(".follow-up-answer").count() == 1
                follow_ups.first.click()
            source_turns = page.locator(".topic-source-turn")
            assert source_turns.count() >= 1, "current topic sources should be visible below the review"
            assert page.locator(".source-drawer").count() == 0, "topic sources should not open in a drawer"
            assert page.locator("[data-segment-role]").count() >= 1, "source structure should allow speaker correction"
            assert page.locator("[data-source-assignment]").count() >= 1, "source structure should allow question/answer reassignment"
            boundary_toggles = page.locator("[data-toggle-boundary]")
            if boundary_toggles.count():
                boundary_toggles.first.click()
                assert page.locator("[data-split-mode]").count() == 1, "boundary editor should explain how split parts are assigned"
                assert page.locator("[data-split-boundary]").count() >= 1, "boundary editor should expose clickable atom boundaries"
                boundary_toggles.first.click()
            assert page.locator(".topic-review-footer").count() == 1, "pending review should end with topic actions"
            assert page.locator("#openReviewDialog").count() == 1, "review header should expose one Agent entry point"
            assert not page.locator("#reviewDialog").is_visible(), "Agent settings should stay closed until requested"
            assert page.locator("#locatePending").count() == 0, "review header should only expose the primary Agent action"
            nav_item = page.locator(".question-nav-item").first
            assert nav_item.locator(".question-nav-primary").count() == 1
            assert nav_item.locator(".question-nav-secondary").count() == 1
            assert 76 <= nav_item.bounding_box()["height"] <= 80
            agent_box = page.locator("#openReviewDialog").bounding_box()
            workspace_box = page.locator(".topic-workspace").bounding_box()
            assert agent_box and workspace_box
            assert abs((agent_box["x"] + agent_box["width"]) - (workspace_box["x"] + workspace_box["width"])) <= 1
            assert_no_overflow(page, "desktop parse page")
            page.screenshot(path=OUTPUT_DIR / "ui-parse-desktop.png", full_page=True)

            parse_url = page.url
            records_snapshot = page.evaluate("localStorage.getItem('offer-radar-agent-v1')")
            snapshot_records = json.loads(records_snapshot)
            target_record = next(item for item in snapshot_records if item["id"] in parse_url)
            target_record.pop("parseRunId", None)
            source_topic = target_record.get("topics", [{}])[0]
            if source_topic.get("followUps"):
                source_topic["followUps"][-1]["needsConfirmation"] = True
                source_topic["followUps"][-1]["confidence"] = "high"
            merge_target = json.loads(json.dumps(source_topic, ensure_ascii=False))
            merge_target["id"] = "ui-merge-target"
            merge_target["title"] = "数据分析：实验结果判断"
            merge_target["followUps"] = []
            merge_target["mainTurn"]["id"] = "ui-merge-target"
            merge_target["mainTurn"]["topicRootId"] = "ui-merge-target"
            merge_target["mainTurn"]["parentQuestionId"] = None
            merge_target["mainTurn"]["order"] = 99
            merge_target["mainTurn"]["confidence"] = "high"
            merge_target["mainTurn"]["needsConfirmation"] = False
            target_record["topics"].append(merge_target)
            records_snapshot = json.dumps(snapshot_records, ensure_ascii=False)
            mobile_review = browser.new_context(viewport={"width": 390, "height": 844})
            mobile_review_page = mobile_review.new_page()
            mobile_review_page.goto(BASE_URL, wait_until="domcontentloaded")
            mobile_review_page.evaluate(
                "value => localStorage.setItem('offer-radar-agent-v1', value)",
                records_snapshot,
            )
            mobile_review_page.goto(parse_url, wait_until="domcontentloaded")
            mobile_review_page.reload(wait_until="networkidle")
            mobile_review_page.locator(".topic-editor").wait_for()
            mobile_follow_ups = mobile_review_page.locator(".follow-up-question")
            assert all(value == "false" for value in mobile_follow_ups.evaluate_all("nodes => nodes.map(node => node.getAttribute('aria-expanded'))")), "needs-confirmation follow-ups should still start collapsed"
            priority_item = mobile_review_page.locator(".question-nav-item.is-priority")
            assert priority_item.count() == 1
            assert "识别置信度：中" in priority_item.inner_text()
            assert "需要重点校对" in priority_item.inner_text()
            assert "重点原因：" in priority_item.locator(".question-nav-review-state").get_attribute("title")
            priority_filter = mobile_review_page.locator('[data-review-filter="priority"]')
            assert "1" in priority_filter.inner_text()
            priority_filter.click()
            assert mobile_review_page.locator(".question-nav-item").count() == 1
            reason = mobile_review_page.locator(".review-reason")
            assert reason.count() == 1
            assert "问答边界或分类待确认" in reason.inner_text()
            merge_button = mobile_review_page.locator("#openMergeTopic")
            assert merge_button.count() == 1
            mobile_heading_metrics = mobile_review_page.evaluate(
                """() => {
                    const titleRow = document.querySelector('.topic-title-row').getBoundingClientRect();
                    const controls = document.querySelector('.topic-heading-controls').getBoundingClientRect();
                    const select = document.querySelector('[data-topic-type]').getBoundingClientRect();
                    const merge = document.querySelector('#openMergeTopic').getBoundingClientRect();
                    const mergeStyle = getComputedStyle(document.querySelector('#openMergeTopic'));
                    const mergeIcon = document.querySelector('#openMergeTopic .lucide-icon').getBoundingClientRect();
                    return {
                        statusInsideTitle: document.querySelector('.topic-title-row').contains(document.querySelector('.topic-state.prominent')),
                        controlsBelowTitle: controls.top > titleRow.bottom,
                        controlsAligned: Math.abs(select.bottom - merge.bottom) <= 1,
                        mergeHeight: merge.height,
                        mergePaddingLeft: mergeStyle.paddingLeft,
                        mergeFontSize: mergeStyle.fontSize,
                        mergeFontWeight: mergeStyle.fontWeight,
                        mergeIconWidth: mergeIcon.width,
                    };
                }"""
            )
            assert mobile_heading_metrics["statusInsideTitle"]
            assert mobile_heading_metrics["controlsBelowTitle"]
            assert mobile_heading_metrics["controlsAligned"]
            assert 33 <= mobile_heading_metrics["mergeHeight"] <= 35
            assert mobile_heading_metrics["mergePaddingLeft"] == "10px"
            assert mobile_heading_metrics["mergeFontSize"] == "13px"
            assert mobile_heading_metrics["mergeFontWeight"] == "600"
            assert 15 <= mobile_heading_metrics["mergeIconWidth"] <= 16
            merge_button.click()
            merge_panel = mobile_review_page.locator(".topic-merge-panel")
            assert merge_panel.is_visible()
            mobile_review_page.screenshot(path=OUTPUT_DIR / "ui-merge-mobile.png", full_page=True)
            assert "当前主问题和 2 个追问将作为追问加入目标主题" in merge_panel.inner_text()
            merge_confirm = mobile_review_page.locator("#mergeTopic")
            assert merge_confirm.is_disabled()
            mobile_review_page.locator('input[name="mergeTarget"]').check()
            assert merge_confirm.is_enabled()
            assert_no_overflow(mobile_review_page, "mobile merge popover")
            merge_confirm.click()
            assert "主题已合并" in mobile_review_page.locator(".toast").inner_text()
            mobile_review_page.locator(".toast-action").click()
            assert "已撤销主题合并" in mobile_review_page.locator(".toast").inner_text()
            assert mobile_review_page.locator(".topic-review-footer").count() == 1
            assert mobile_review_page.locator("#openReviewDialog").count() == 1
            assert_no_overflow(mobile_review_page, "mobile parse page")
            mobile_review_page.screenshot(path=OUTPUT_DIR / "ui-parse-mobile.png", full_page=True)
            mobile_review.close()

            page.locator("#openReviewDialog").click()
            dialog = page.locator("#reviewDialog")
            assert dialog.is_visible()
            assert "未经校对" in dialog.inner_text()
            assert page.locator("#startRun").is_disabled(), "quick review requires source choice and acknowledgement"
            page.get_by_role("radio", name=re.compile("仅使用内部资料")).check()
            assert page.locator("#startRun").is_disabled(), "unreviewed cards still require explicit acknowledgement"
            page.get_by_role("checkbox", name=re.compile("未校对内容会在报告中标记")).check()
            assert page.locator("#startRun").is_enabled(), "quick review should unlock after explicit choices"
            page.screenshot(path=OUTPUT_DIR / "ui-dialog-desktop.png")
            page.set_viewport_size({"width": 390, "height": 844})
            assert dialog.is_visible()
            assert_no_overflow(page, "mobile Agent dialog")
            page.screenshot(path=OUTPUT_DIR / "ui-dialog-mobile.png")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.locator("#startRun").click()
            page.wait_for_url(re.compile(r"#/review/"), timeout=25_000)
            assert "HelloAgents" not in page.locator(".report-page").inner_text()
            assert "快速复盘" in page.locator(".mode-badge.quick").inner_text()
            assert page.get_by_role("heading", name="面试综合评价").is_visible()
            assert page.get_by_role("heading", name="技能 / 知识缺口").is_visible()
            assert page.get_by_role("heading", name="下一步行动计划").is_visible()
            assert page.get_by_role("heading", name="逐题深度复盘").is_visible()
            assert page.locator(".legacy-report-notice").count() == 0
            assert "旧报告未" not in page.locator(".report-page").inner_text()
            assert page.locator(".action-item").count() == 3
            assert page.locator(".action-deliverable").count() == 0
            assert page.locator(".action-order").first.inner_text() == "行动 1"
            assert page.locator(".action-meta").first.inner_text().startswith("提升维度：")
            assert page.locator(".gap-impact").count() == 0
            assert page.locator(".gap-topics").evaluate_all(
                "groups => groups.every(group => { const labels = [...group.querySelectorAll('button')].map(button => button.innerText); return labels.length === new Set(labels).size; })"
            ), "gap topic actions need distinct labels within each gap"
            rerun_review = page.locator("#rerunReview")
            assert rerun_review.is_visible()
            assert rerun_review.inner_text().strip() == "重新面试复盘"
            rerun_review.click()
            rerun_dialog = page.locator("#reviewDialog")
            assert rerun_dialog.is_visible()
            assert page.get_by_role("heading", name="重新进行面试复盘").is_visible()
            assert "基于当前题卡重新运行 Agent" in rerun_dialog.inner_text()
            rerun_dialog.get_by_role("radio", name=re.compile("仅使用内部资料")).check()
            rerun_acknowledgement = rerun_dialog.locator("#acknowledgeUnreviewed")
            if rerun_acknowledgement.count():
                rerun_acknowledgement.check()
            assert rerun_dialog.locator("#startRun").is_enabled()
            rerun_dialog.get_by_role("button", name="取消").click()
            rerun_dialog.wait_for(state="hidden")
            page.locator(".accordion-button").first.click()
            page.locator(".accordion-panel").wait_for()
            tabs = page.locator(".question-review-tabs .question-review-tab")
            assert tabs.count() == 5
            tab_labels = tabs.all_inner_texts()
            assert tab_labels[:4] == ["回答逻辑", "面试官信号", "问题诊断", "优化回答"]
            assert tab_labels[4].startswith("证据引用")
            page.get_by_role("tab", name="回答逻辑", exact=True).click()
            reading = page.locator(".question-reading-details")
            assert reading.locator("summary").inner_text().startswith("回答原文")
            reading.locator("summary").click()
            assert reading.locator(".answer-transcript-list > section").count() >= 1
            page.screenshot(path=OUTPUT_DIR / "ui-question-logic-desktop.png", full_page=True)
            for tab_name in ("回答逻辑", "面试官信号", "问题诊断", "优化回答", "证据引用"):
                page.get_by_role("tab", name=tab_name, exact=False).first.click()
                if tab_name == "问题诊断":
                    panel = page.locator(".diagnosis-tab-panel")
                    assert panel.get_by_text("查看完整 Agent 诊断", exact=True).count() == 0
                    assert panel.locator(".diagnosis-uncertainty").count() == 0
                if tab_name == "优化回答":
                    assert page.locator(".framework-heading").is_visible()
                    assert page.locator(".optimized-answer").is_visible()
                    assert page.locator(".answer-outline-details").is_visible()
                    assert not page.locator(".answer-outline-details").evaluate("node => node.open")
                    assert page.locator(".answer-provenance-note").is_visible()
                    assert page.locator(".improvement-notice").count() == 0
                    framework_metrics = page.evaluate(
                        """() => ({
                            noticeHeight: document.querySelector('.answer-provenance-note').getBoundingClientRect().height,
                            keys: [...document.querySelectorAll('.framework-key')].map(node => ({
                                width: node.getBoundingClientRect().width,
                                scrollWidth: node.scrollWidth,
                                clientWidth: node.clientWidth,
                            })),
                        })"""
                    )
                    assert framework_metrics["noticeHeight"] <= 40
                    page.screenshot(path=OUTPUT_DIR / "ui-question-improvement-desktop.png", full_page=True)
                    page.locator(".answer-outline-details > summary").click()
                    assert page.locator(".answer-outline-details").evaluate("node => node.open")
                    framework_metrics = page.evaluate(
                        """() => [...document.querySelectorAll('.framework-key')].map(node => ({
                            width: node.getBoundingClientRect().width,
                            scrollWidth: node.scrollWidth,
                            clientWidth: node.clientWidth,
                        }))"""
                    )
                    assert len({round(item["width"]) for item in framework_metrics}) <= 1
                    assert all(item["scrollWidth"] <= item["clientWidth"] for item in framework_metrics)
                    page.locator(".answer-outline-details > summary").click()
                    assert not page.locator(".answer-outline-details").evaluate("node => node.open")
                assert_no_overflow(page, f"desktop report {tab_name} tab")
            assert page.locator(".evidence-overview").is_visible()
            gap_topic = page.locator("[data-open-topic]").first
            if gap_topic.count():
                gap_topic.click()
                assert page.locator(".accordion-item.expanded").count() == 1
            with page.expect_download() as download_info:
                page.locator("#exportReport").click()
            markdown = Path(download_info.value.path()).read_text(encoding="utf-8")
            for heading in (
                "## 面试综合评价", "## 技能 / 知识缺口", "## 下一步行动计划",
                "## 逐题深度复盘", "**回答逻辑**", "**面试官信号**",
                "**问题诊断**", "**回答改进**", "**证据引用**",
            ):
                assert heading in markdown
            assert_no_overflow(page, "desktop report page")
            page.screenshot(path=OUTPUT_DIR / "ui-report-desktop.png", full_page=True)

            report_url = page.url
            page.set_viewport_size({"width": 390, "height": 844})
            assert_no_overflow(page, "mobile V2 report page")
            assert page.locator(".question-review-tabs").evaluate(
                "node => node.scrollWidth >= node.clientWidth"
            )
            assert page.locator(".action-list").evaluate(
                "node => getComputedStyle(node).gridTemplateColumns.split(' ').length === 1"
            )
            page.screenshot(path=OUTPUT_DIR / "ui-report-mobile.png", full_page=True)
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(f"{BASE_URL}/#/trends", wait_until="networkidle")
            add_trend = page.locator("#openTrendImport")
            assert add_trend.is_visible(), "growth trends should expose a manual import entry point"
            add_trend.click()
            trend_dialog = page.locator("#trendImportDialog")
            trend_dialog.wait_for(state="visible")
            page.wait_for_function(
                "() => !document.querySelector('.trend-import-empty')?.textContent.includes('正在读取')",
                timeout=5_000,
            )
            candidates = trend_dialog.locator("input[data-select-trend-candidate]")
            available_candidate = trend_dialog.locator("input[data-select-trend-candidate]:not([disabled])").first
            if candidates.count() == 0:
                assert "没有可添加的面试记录" in trend_dialog.inner_text()
            elif available_candidate.count():
                candidate_id = available_candidate.get_attribute("data-select-trend-candidate")
                available_candidate.check()
                assert page.locator("#importSelectedTrends").is_enabled()
                page.locator("#importSelectedTrends").click()
                trend_dialog.wait_for(state="hidden")
                assert page.locator(".trend-item").count() >= 1
                add_trend.click()
                trend_dialog.wait_for(state="visible")
                imported_candidate = trend_dialog.locator(f'input[data-select-trend-candidate="{candidate_id}"]')
                assert imported_candidate.is_disabled()
                assert "已添加" in imported_candidate.locator("xpath=ancestor::label").inner_text()
            else:
                assert all(candidates.evaluate_all("nodes => nodes.map(node => node.disabled)"))
            assert_no_overflow(page, "desktop growth import dialog")
            page.screenshot(path=OUTPUT_DIR / "ui-trends-import-desktop.png", full_page=True)
            page.locator("[data-close-trend-import]").first.click()
            page.goto(report_url, wait_until="networkidle")
            assert page.locator("#editReportCards").is_visible()

            page.locator("#editReportCards").click()
            page.wait_for_url(re.compile(r"#/parse/.+\?from=report"), timeout=10_000)
            page.locator(".topic-editor").wait_for(timeout=10_000)
            assert page.locator("#returnToReport").is_visible()
            regenerate = page.locator("#openReviewDialog")
            assert regenerate.inner_text() == "重新生成复盘报告"
            assert regenerate.is_disabled(), "report regeneration should stay disabled before a real edit"
            page.locator(".main-turn-edit").click()
            page.get_by_role("button", name="保存修改").click()
            assert regenerate.is_disabled(), "saving unchanged text must not invalidate the report"
            page.locator(".main-turn-edit").click()
            editor = page.locator('.turn-edit-form [data-edit-field="question"]')
            editor.fill(f"{editor.input_value()}（补充）")
            page.get_by_role("button", name="保存修改").click()
            assert regenerate.is_enabled(), "a real card edit should enable report regeneration"
            page.locator("#returnToReport").click()
            page.wait_for_url(re.compile(r"#/review/"), timeout=10_000)
            assert page.locator("#editReportCards").is_visible()

            run_page = desktop.new_page()
            run_page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            run_payload = {
                "id": "ui-agent-run",
                "status": "FAILED",
                "phase": "failed",
                "agent_mode": "helloagents",
                "degraded": False,
                "error": "两轮 Reflection 审计后仍存在关键问题",
                "failure_code": "AUDIT_CRITICAL",
                "events": [
                    {"id": 1, "type": "TOPIC_ANALYSIS_COMPLETED", "data": {"topicId": "topic-1", "evidenceCount": 3}, "createdAt": "2026-08-09T10:00:00Z"},
                    {"id": 2, "type": "AUDIT_COMPLETED", "data": {"round": 2, "findingCount": 1}, "createdAt": "2026-08-09T10:01:00Z"},
                    {"id": 3, "type": "RUN_FAILED", "data": {"code": "AUDIT_CRITICAL", "message": "两轮 Reflection 审计后仍存在关键问题"}, "createdAt": "2026-08-09T10:02:00Z"},
                ],
                "progress": {
                    "completedTopics": 2,
                    "auditRound": 2,
                    "revisionCount": 1,
                    "checkpoint": {"completedTopicIds": ["topic-1", "topic-2"], "evidenceComplete": True},
                },
                "artifacts": [],
            }
            run_page.route(
                "**/api/v1/runs/ui-agent-run",
                lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(run_payload, ensure_ascii=False)),
            )
            run_page.goto(BASE_URL, wait_until="domcontentloaded")
            run_records = json.loads(run_page.evaluate("localStorage.getItem('offer-radar-agent-v1')"))
            run_record = next(item for item in run_records if item["id"] in page.url)
            run_record["runId"] = "ui-agent-run"
            run_record["status"] = "failed"
            run_record["phase"] = "failed"
            run_record["topics"] = [{"id": "topic-1"}, {"id": "topic-2"}]
            run_record["questionCount"] = 2
            run_page.evaluate(
                "value => localStorage.setItem('offer-radar-agent-v1', value)",
                json.dumps(run_records, ensure_ascii=False),
            )
            run_page.close()
            run_page = desktop.new_page()
            run_page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            run_page.route(
                "**/api/v1/runs/ui-agent-run",
                lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(run_payload, ensure_ascii=False)),
            )
            run_page.goto(f"{BASE_URL}/#/run/{run_record['id']}/ui-agent-run", wait_until="networkidle")
            run_summary = run_page.locator(".agent-run-summary").inner_text()
            assert "HelloAgents" not in run_summary
            assert "2/2 个主题已提交" in run_summary, run_summary
            assert "Reflection 审计 2/2 轮 · 已修订 1 次" in run_summary
            assert run_page.locator(".agent-stage.done").count() == 1
            assert run_page.locator(".agent-stage.error").count() == 1
            assert run_page.locator("#resumeRun").is_visible()
            assert run_page.locator("#fallbackRun").is_visible()
            assert "AUDIT_CRITICAL" in run_page.locator(".run-failure").inner_text()
            assert_no_overflow(run_page, "desktop failed Agent run page")
            run_page.screenshot(path=OUTPUT_DIR / "ui-agent-run-desktop.png", full_page=True)
            run_page.set_viewport_size({"width": 390, "height": 844})
            assert_no_overflow(run_page, "mobile failed Agent run page")
            run_page.screenshot(path=OUTPUT_DIR / "ui-agent-run-mobile.png", full_page=True)
            desktop.close()

            mobile = browser.new_context(viewport={"width": 390, "height": 844})
            mobile_page = mobile.new_page()
            mobile_page.goto(f"{BASE_URL}/#/new", wait_until="networkidle")
            assert_no_overflow(mobile_page, "mobile new page")
            mobile_page.screenshot(path=OUTPUT_DIR / "ui-new-mobile.png", full_page=True)
            mobile.close()

            if console_errors:
                raise AssertionError(f"console errors: {' | '.join(console_errors)}")
            print(json.dumps({"ok": True, "screenshots": 11, "consoleErrors": 0}))
        finally:
            browser.close()


if __name__ == "__main__":
    run()
