from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

try:
    from hello_agents.tools.base import Tool, ToolParameter
    from hello_agents.tools.response import ToolResponse
except ImportError:  # pragma: no cover
    class Tool:
        def __init__(self, name: str, description: str, expandable: bool = False):
            self.name, self.description, self.expandable = name, description, expandable

    class ToolParameter:
        def __init__(self, **kwargs: Any):
            self.__dict__.update(kwargs)

    class ToolResponse:
        @staticmethod
        def success(text: str, data: Any = None, stats: Any = None):
            return {"status": "success", "text": text, "data": data, "stats": stats}

        @staticmethod
        def partial(text: str, data: Any = None, stats: Any = None):
            return {"status": "partial", "text": text, "data": data, "stats": stats}

        @staticmethod
        def error(code: str, message: str, stats: Any = None, context: Any = None):
            return {"status": "error", "error": {"code": code, "message": message}}


class ParseToolContext(Protocol):
    material_id: str
    parse_run_id: str

    def inspect(self) -> dict[str, Any]: ...
    def transcribe(self) -> dict[str, Any]: ...
    def validate(self) -> dict[str, Any]: ...
    def structure(self) -> dict[str, Any]: ...
    def submit(self) -> dict[str, Any]: ...


class MaterialParams(BaseModel):
    material_id: str


class RunParams(BaseModel):
    parse_run_id: str


class ScopedParseTool(Tool):
    def __init__(self, name: str, description: str, context: ParseToolContext, scope: str):
        super().__init__(name=name, description=description, expandable=False)
        self.context = context
        self.scope = scope

    def get_parameters(self) -> list[ToolParameter]:
        if self.scope == "material":
            return [ToolParameter(name="material_id", type="string", description="当前解析任务的材料 ID", required=True)]
        return [ToolParameter(name="parse_run_id", type="string", description="当前解析任务 ID", required=True)]

    def _check(self, parameters: dict[str, Any]) -> ToolResponse | None:
        try:
            if self.scope == "material":
                value = MaterialParams.model_validate(parameters).material_id
                expected = self.context.material_id
            else:
                value = RunParams.model_validate(parameters).parse_run_id
                expected = self.context.parse_run_id
        except ValidationError as exc:
            return ToolResponse.error("INVALID_PARAMETERS", str(exc))
        if value != expected:
            return ToolResponse.error("OUT_OF_SCOPE", "工具只能访问当前解析任务绑定的 ID")
        return None


class InspectMaterialTool(ScopedParseTool):
    def __init__(self, context: ParseToolContext):
        super().__init__("InspectMaterial", "检查当前材料的格式、大小、哈希和音频时长。", context, "material")

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        error = self._check(parameters)
        if error:
            return error
        data = self.context.inspect()
        return ToolResponse.success("材料检查完成", data=data)


class DeepgramTranscriptionTool(ScopedParseTool):
    def __init__(self, context: ParseToolContext):
        super().__init__("DeepgramTranscription", "将当前音频发送到 Deepgram，返回带时间戳和说话人编号的 artifact。", context, "material")

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        error = self._check(parameters)
        if error:
            return error
        data = self.context.transcribe()
        return ToolResponse.success("Deepgram 转写完成", data=data)


class TranscriptValidationTool(ScopedParseTool):
    def __init__(self, context: ParseToolContext):
        super().__init__("TranscriptValidation", "检查片段置信度、时间戳、重复、空白和说话人数量。", context, "run")

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        error = self._check(parameters)
        if error:
            return error
        data = self.context.validate()
        if data.get("blockingIssueCount", 0):
            return ToolResponse.partial("转写校验发现阻塞问题", data=data)
        return ToolResponse.success("转写校验完成", data=data)


class TranscriptStructuringTool(ScopedParseTool):
    def __init__(self, context: ParseToolContext):
        super().__init__("TranscriptStructuring", "分块识别说话人角色、主问题、回答和追问关系。", context, "run")

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        error = self._check(parameters)
        if error:
            return error
        data = self.context.structure()
        return ToolResponse.success("语义拆题完成", data=data)


class SubmitQuestionCardsTool(ScopedParseTool):
    def __init__(self, context: ParseToolContext):
        super().__init__("SubmitQuestionCards", "提交经过片段引用和父子关系校验的候选题卡 artifact。", context, "run")
        self.last_submission: dict[str, Any] | None = None

    def run(self, parameters: dict[str, Any]) -> ToolResponse:
        error = self._check(parameters)
        if error:
            return error
        data = self.context.submit()
        self.last_submission = data if data.get("accepted") else None
        if not data.get("accepted"):
            return ToolResponse.partial("候选题卡未通过校验", data=data)
        return ToolResponse.success("候选题卡已通过校验", data=data)


def build_parse_tools(context: ParseToolContext) -> tuple[list[Tool], SubmitQuestionCardsTool]:
    submit = SubmitQuestionCardsTool(context)
    return [
        InspectMaterialTool(context),
        DeepgramTranscriptionTool(context),
        TranscriptValidationTool(context),
        TranscriptStructuringTool(context),
        submit,
    ], submit
