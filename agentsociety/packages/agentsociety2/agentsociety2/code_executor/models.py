"""
程式碼執行器的資料模型定義

使用Pydantic進行資料驗證和序列化。
"""

from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class CodeGenerationRequest(BaseModel):
    """
    程式碼生成請求模型

    包含生成程式碼所需的所有引數。
    """

    description: str = Field(..., description="程式碼生成的要求描述")
    """
    程式碼生成的要求描述，詳細說明需要生成什麼樣的程式碼。
    """

    input_files: Optional[list[str]] = Field(
        default=None,
        description="輸入檔案路徑列表（可選）",
    )
    """
    輸入檔案路徑列表，用於提供上下文或參考檔案。
    如果提供，這些檔案的內容會被包含在生成提示中。
    """

    additional_context: Optional[str] = Field(
        default=None,
        description="額外的上下文資訊（可選）",
    )
    """
    額外的上下文資訊，用於補充描述。
    """

    save_path: Optional[str] = Field(
        default=None,
        description="程式碼儲存路徑（可選，如果為None則使用預設路徑）",
    )
    """
    程式碼儲存路徑。
    如果未指定此路徑，將使用預設路徑。
    """

    model_config = ConfigDict(extra="forbid")


class ExecutionResult(BaseModel):
    """
    程式碼執行結果模型

    包含程式碼執行的輸出、錯誤資訊等。
    """

    success: bool = Field(..., description="執行是否成功")
    """
    執行是否成功。
    """

    stdout: str = Field(default="", description="標準輸出內容")
    """
    標準輸出內容。
    """

    stderr: str = Field(default="", description="標準錯誤輸出內容")
    """
    標準錯誤輸出內容。
    """

    return_code: int = Field(default=0, description="返回碼")
    """
    執行返回碼。0通常表示成功，非0表示失敗。
    """

    execution_time: float = Field(default=0.0, description="執行耗時（秒）")
    artifacts_path: Optional[str] = Field(
        default=None, description="執行產物儲存目錄（如有）"
    )
    artifacts: list[str] = Field(
        default_factory=list,
        description="執行產物列表（相對 artifacts_path 的相對路徑，如無則為空）",
    )
    """
    程式碼執行耗時，單位為秒。
    """

    model_config = ConfigDict(extra="forbid")


class CodeGenerationResponse(BaseModel):
    """
    程式碼生成響應模型

    包含生成的程式碼、執行結果等資訊。
    """

    generated_code: str = Field(..., description="生成的程式碼")
    """
    大模型生成的程式碼內容。
    """

    detected_dependencies: list[str] = Field(
        default_factory=list,
        description="檢測到的依賴包列表",
    )
    """
    從生成的程式碼中檢測到的Python依賴包列表。
    """

    execution_result: Optional[ExecutionResult] = Field(
        default=None,
        description="程式碼執行結果（如果執行了）",
    )
    """
    程式碼執行結果。
    如果程式碼被執行，此欄位包含執行輸出和錯誤資訊。
    """

    saved_path: Optional[str] = Field(
        default=None,
        description="程式碼儲存路徑",
    )
    """
    程式碼儲存的檔案路徑。
    如果使用預設儲存目錄且未顯式指定save_path，此欄位為自動生成的檔案路徑。
    """

    model_config = ConfigDict(extra="forbid")
