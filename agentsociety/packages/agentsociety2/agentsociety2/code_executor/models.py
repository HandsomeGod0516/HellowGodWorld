"""
代码执行器的数据模型定义

使用Pydantic进行数据验证和序列化。
"""

from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class CodeGenerationRequest(BaseModel):
    """
    代码生成请求模型

    包含生成代码所需的所有参数。
    """

    description: str = Field(..., description="代码生成的要求描述")
    """
    代码生成的要求描述，详细说明需要生成什么样的代码。
    """

    input_files: Optional[list[str]] = Field(
        default=None,
        description="输入文件路径列表（可选）",
    )
    """
    输入文件路径列表，用于提供上下文或参考文件。
    如果提供，这些文件的内容会被包含在生成提示中。
    """

    additional_context: Optional[str] = Field(
        default=None,
        description="额外的上下文信息（可选）",
    )
    """
    额外的上下文信息，用于补充描述。
    """

    save_path: Optional[str] = Field(
        default=None,
        description="代码保存路径（可选，如果为None则使用默认路径）",
    )
    """
    代码保存路径。
    如果未指定此路径，将使用默认路径。
    """

    model_config = ConfigDict(extra="forbid")


class ExecutionResult(BaseModel):
    """
    代码执行结果模型

    包含代码执行的输出、错误信息等。
    """

    success: bool = Field(..., description="执行是否成功")
    """
    执行是否成功。
    """

    stdout: str = Field(default="", description="标准输出内容")
    """
    标准输出内容。
    """

    stderr: str = Field(default="", description="标准错误输出内容")
    """
    标准错误输出内容。
    """

    return_code: int = Field(default=0, description="返回码")
    """
    执行返回码。0通常表示成功，非0表示失败。
    """

    execution_time: float = Field(default=0.0, description="执行耗时（秒）")
    artifacts_path: Optional[str] = Field(
        default=None, description="执行产物保存目录（如有）"
    )
    artifacts: list[str] = Field(
        default_factory=list,
        description="执行产物列表（相对 artifacts_path 的相对路径，如无则为空）",
    )
    """
    代码执行耗时，单位为秒。
    """

    model_config = ConfigDict(extra="forbid")


class CodeGenerationResponse(BaseModel):
    """
    代码生成响应模型

    包含生成的代码、执行结果等信息。
    """

    generated_code: str = Field(..., description="生成的代码")
    """
    大模型生成的代码内容。
    """

    detected_dependencies: list[str] = Field(
        default_factory=list,
        description="检测到的依赖包列表",
    )
    """
    从生成的代码中检测到的Python依赖包列表。
    """

    execution_result: Optional[ExecutionResult] = Field(
        default=None,
        description="代码执行结果（如果执行了）",
    )
    """
    代码执行结果。
    如果代码被执行，此字段包含执行输出和错误信息。
    """

    saved_path: Optional[str] = Field(
        default=None,
        description="代码保存路径",
    )
    """
    代码保存的文件路径。
    如果使用默认保存目录且未显式指定save_path，此字段为自动生成的文件路径。
    """

    model_config = ConfigDict(extra="forbid")
