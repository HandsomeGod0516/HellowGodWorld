"""
環境路由器的指令測試基準評測系統

該模組實現了對環境路由器（env_router）效能的全面評測，透過執行預定義的測試指令集，
評估路由器在工具選擇、呼叫序列和引數傳遞等方面的準確性。

評測指標說明：
=============

1. 工具選擇準確度（Tool Selection IOU）
   - 衡量是否選擇了正確的工具函式
   - 使用交併比（IOU）計算期望函式集合和實際函式集合的重疊程度
   - 範圍：[0, 1]，1.0 表示完全匹配
   - 不考慮呼叫順序，只關注函式集合

2. 呼叫序列準確度（Sequence LCS Score）
   - 衡量呼叫順序是否正確
   - 使用最長公共子序列（LCS）計算實際序列和期望序列的匹配程度
   - 範圍：[0, 1]，1.0 表示期望序列是實際序列的子序列
   - 歸一化：LCS長度 / 期望序列長度

3. 引數準確度（Parameter Accuracy）
   - 衡量每個函式呼叫的引數是否正確傳遞
   - 對每個期望呼叫，找到最佳匹配的實際呼叫，計算引數匹配準確度
   - 範圍：[0, 1]，1.0 表示所有引數都完全匹配
   - 支援萬用字元 '*'（跳過匹配）和上下文標記 '@'（匹配 context['id']）

4. 成功呼叫（Successful Call）
   - 綜合評估：完全成功需要同時滿足：
     * 沒有異常發生
     * 呼叫序列完全正確（LCS = 1.0）
     * 所有引數都完全匹配
   - 布林值：True 表示完全成功，False 表示至少有一個條件不滿足

異常處理：
=========
- 異常呼叫會被排除在 IOU、LCS 和引數匹配的計算之外
- 但 token 使用統計仍然包括所有呼叫（包括異常呼叫）
- 如果發生異常，has_exception 會被設定為 True

特殊標記：
=========
- '*'：萬用字元，表示該引數的值不重要，不參與匹配
- '@'：上下文標記，表示該引數應該匹配 context 中的 'id' 欄位
  例如：{"person_id": "@"} 會被替換為 {"person_id": context["id"]}

使用示例：
=========
```python
# 載入測試資料
with open("instruction_test.yaml", "r") as f:
    test_data = yaml.safe_load(f)

# 對每個測試用例計算指標
for test_case in test_data["instructions"]:
    expected_calls = test_case["expected_calls"]
    context = {"id": 123}
    
    # 執行路由器的 ask 方法
    result, answer = await router.ask(context, test_case["instruction"])
    
    # 提取實際呼叫
    tool_call_history = router.get_tool_call_history()
    actual_calls = extract_call_signatures(tool_call_history, exclude_exceptions=True)
    
    # 計算指標
    metrics = compute_metrics(expected_calls, actual_calls, tool_call_history, context)
    
    print(f"工具選擇準確度: {metrics['tool_selection_iou']:.2f}")
    print(f"序列準確度: {metrics['sequence_lcs_score']:.2f}")
    print(f"引數準確度: {metrics['param_accuracy']:.2f}")
    print(f"是否成功: {metrics['successful_call']}")
```
"""

import asyncio
import json
import logging
# ruff: noqa: E402

import os
import pickle
import re
import time
import yaml
from datetime import datetime
from typing import List, Dict, Any, Tuple, Set

from dotenv import load_dotenv
load_dotenv(".env.openrouter")

from agentsociety2.contrib.env.event_space import EventSpace
from agentsociety2.contrib.env.mobility_space import MobilitySpace
from agentsociety2.contrib.env.social_media import SocialMediaSpace
from agentsociety2.env import (
    CodeGenRouter,
    SearchToolRouter,
    TwoTierPlanExecuteRouter,
    TwoTierReActRouter,
)
from agentsociety2.logger import setup_logging, get_logger
from agentsociety2.config import get_model_name
from tqdm import tqdm


# 僅在此處集中記錄基準評測所用的模型名稱（不包含任何 API Key）
# 與 config 中 coder 模型一致，其他模組可直接從這裡讀取
CODER_MODEL_NAME: str = get_model_name("coder")


def compute_iou(set1: Set, set2: Set) -> float:
    """
    計算兩個集合的交併比（Intersection over Union, IOU）。
    
    該指標用於評估工具選擇的準確性，透過比較期望呼叫的函式集合和實際呼叫的函式集合的重疊程度。
    
    計算公式：IOU = |set1 ∩ set2| / |set1 ∪ set2|
    
    引數:
        set1: 第一個集合（通常是期望的函式名集合）
        set2: 第二個集合（通常是實際的函式名集合）
    
    返回:
        float: IOU 值，範圍 [0, 1]
        - 1.0 表示兩個集合完全相同
        - 0.0 表示兩個集合沒有交集
        - 當兩個集合都為空時，返回 1.0（表示都正確，因為沒有需要呼叫的函式）
        - 當只有一個集合為空時，返回 0.0（表示完全不匹配）
    
    示例:
        >>> compute_iou({1, 2, 3}, {2, 3, 4})
        0.5  # 交集 {2, 3} 大小為 2，並集 {1, 2, 3, 4} 大小為 4，IOU = 2/4 = 0.5
    """
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 1.0  # 兩個集合都為空時返回 1.0


def compute_lcs_score(seq1: List, seq2: List) -> float:
    """
    計算歸一化的最長公共子序列（Longest Common Subsequence, LCS）分數。
    
    該指標用於評估呼叫序列的準確性，透過比較實際呼叫序列和期望呼叫序列的最長公共子序列長度。
    分數 = LCS長度 / 期望序列長度
    
    注意：這裡使用歸一化分數，即 LCS 長度除以期望序列長度，而不是實際序列長度。
    這樣可以衡量實際序列在多大程度上"覆蓋"了期望序列。
    
    引數:
        seq1: 實際序列（actual sequence）
        seq2: 期望序列（expected sequence）
    
    返回:
        float: 歸一化的 LCS 分數，範圍 [0, 1]
        - 1.0 表示期望序列是實際序列的子序列（完全匹配或超出期望）
        - 0.0 表示兩個序列沒有公共子序列
        - 當期望序列為空時，如果實際序列也為空返回 1.0，否則返回 0.0
    
    示例:
        >>> compute_lcs_score(['A', 'B', 'C', 'D'], ['A', 'C', 'D'])
        1.0  # LCS 是 ['A', 'C', 'D']，長度為 3，期望序列長度為 3，分數 = 3/3 = 1.0
        >>> compute_lcs_score(['A', 'B', 'C'], ['A', 'C', 'D'])
        0.67  # LCS 是 ['A', 'C']，長度為 2，期望序列長度為 3，分數 = 2/3 ≈ 0.67
    """
    # 處理邊界情況：期望序列為空
    if not seq2:
        # 如果實際序列也為空，返回 1.0（表示都正確）
        # 如果實際序列不為空，返回 0.0（表示不匹配）
        return 1.0 if not seq1 else 0.0

    # 使用動態規劃計算 LCS 長度
    # dp[i][j] 表示 seq1 的前 i 個元素和 seq2 的前 j 個元素的 LCS 長度
    m, n = len(seq1), len(seq2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    # 填充動態規劃表
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if seq1[i - 1] == seq2[j - 1]:
                # 如果當前元素相同，LCS 長度加 1
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                # 如果當前元素不同，取之前兩種情況的最大值
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    lcs_length = dp[m][n]  # 獲取 LCS 長度
    # 返回歸一化分數：LCS 長度除以期望序列長度
    return lcs_length / len(seq2)


def match_args(
    expected_kwargs: dict, actual_kwargs: dict, context: Dict[str, Any] | None = None
) -> Tuple[bool, float]:
    """
    匹配期望的引數和實際引數，計算引數匹配的準確度。
    
    該函式用於評估函式呼叫時引數傳遞的準確性。支援兩種特殊標記：
    - '*'：萬用字元，表示該引數的值不重要，不參與匹配
    - '@'：上下文標記，表示該引數應該匹配 context 中的 'id' 欄位
    
    引數:
        expected_kwargs: 期望的引數字典，可能包含：
            - '*' 作為萬用字元（跳過該引數的匹配）
            - '@' 作為上下文標記（會被替換為 context['id']）
        actual_kwargs: 實際的引數字典
        context: 上下文字典，包含 'id' 欄位（用於 '@' 標記）
                如果為 None 或 '@' 標記沒有對應的 context，則 '@' 會被視為萬用字元（跳過）
    
    返回:
        Tuple[bool, float]: (是否完全匹配, 匹配準確度)
        - all_match: True 表示所有需要匹配的引數都完全匹配，False 表示至少有一個不匹配
        - accuracy: 匹配準確度，範圍 [0, 1]，計算公式 = 匹配的引數數量 / 需要匹配的引數總數
          （排除萬用字元和無效的 '@' 標記）
    
    示例:
        >>> match_args({"a": 1, "b": "*", "c": 2}, {"a": 1, "b": 999, "c": 2})
        (True, 1.0)  # 'b' 是萬用字元，不參與匹配；'a' 和 'c' 都匹配
        >>> match_args({"a": 1, "c": 2}, {"a": 1, "c": 3})
        (False, 0.5)  # 'a' 匹配，'c' 不匹配，準確度 = 1/2 = 0.5
        >>> match_args({"person_id": "@"}, {"person_id": 123}, {"id": 123})
        (True, 1.0)  # '@' 被替換為 context['id'] = 123，匹配成功
    """
    # 處理邊界情況：期望引數為空
    if not expected_kwargs:
        # 如果實際引數也為空，返回完全匹配
        # 如果實際引數不為空，返回不匹配
        return (True, 1.0) if not actual_kwargs else (False, 0.0)

    matched = 0  # 匹配的引數數量
    total = 0    # 需要匹配的引數總數（排除萬用字元）
    
    for param_name, exp_value in expected_kwargs.items():
        # 跳過萬用字元：'*' 表示該引數的值不重要，不參與匹配
        if exp_value == "*":
            continue
        
        # 處理 '@' 標記：替換為 context 中的 'id' 值
        if exp_value == "@":
            if context is None or "id" not in context:
                # 如果 context 缺失或沒有 'id' 欄位，將 '@' 視為萬用字元（跳過）
                continue
            exp_value = context["id"]  # 替換為實際的 id 值
            
        # 該引數需要參與匹配
        total += 1
        
        # 檢查實際引數中是否存在該引數名
        if param_name in actual_kwargs:
            act_value = actual_kwargs[param_name]
            # 比較期望值和實際值是否相等
            if exp_value == act_value:
                matched += 1  # 匹配成功

    # 計算準確度：匹配數 / 總數
    # 如果 total 為 0（所有引數都是萬用字元），返回 1.0（表示都正確）
    accuracy = matched / total if total > 0 else 1.0
    # 判斷是否完全匹配：匹配數等於總數
    all_match = matched == total
    return (all_match, accuracy)


def extract_call_signatures(
    tool_call_history: List[Dict[str, Any]],
    exclude_exceptions: bool = True,
) -> List[Tuple[str, str, dict]]:
    """
    從工具呼叫歷史中提取呼叫簽名。
    
    該函式將工具呼叫歷史記錄轉換為標準化的呼叫簽名列表，每個簽名包含：
    (模組名, 函式名, 引數字典)
    
    注意：異常呼叫可以根據引數選擇是否排除。在指標計算中，通常需要排除異常呼叫，
    因為異常呼叫表示執行失敗，不應該參與準確性評估。
    
    引數:
        tool_call_history: 工具呼叫歷史記錄列表，每個記錄是一個字典，包含：
            - module_name: 模組名稱
            - function_name: 函式名稱
            - kwargs: 引數字典
            - exception_occurred: 是否發生異常（可選）
        exclude_exceptions: 如果為 True，排除所有 exception_occurred=True 的呼叫
    
    返回:
        List[Tuple[str, str, dict]]: 呼叫簽名列表，每個元素是 (模組名, 函式名, 引數字典) 的元組
    
    示例:
        >>> history = [
        ...     {"module_name": "MobilitySpace", "function_name": "get_person", 
        ...      "kwargs": {"person_id": 123}, "exception_occurred": False},
        ...     {"module_name": "EventSpace", "function_name": "start_event",
        ...      "kwargs": {"person_id": 123}, "exception_occurred": True}
        ... ]
        >>> extract_call_signatures(history, exclude_exceptions=True)
        [("MobilitySpace", "get_person", {"person_id": 123})]
        # 第二個呼叫因為異常被排除
    """
    return [
        (call.get("module_name", ""), call.get("function_name", ""), call.get("kwargs", {}))
        for call in tool_call_history
        if not (exclude_exceptions and call.get("exception_occurred", False))
    ]


def compute_metrics(
    expected_calls: List[List],
    actual_calls: List[Tuple[str, str, dict]],
    tool_call_history: List[Dict[str, Any]] | None = None,
    context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    計算單個測試用例的所有評估指標。
    
    該函式是評測系統的核心，計算以下四個主要指標：
    1. 工具選擇準確度（IOU）：評估是否選擇了正確的工具函式
    2. 呼叫序列準確度（LCS）：評估呼叫順序是否正確
    3. 引數準確度：評估引數傳遞是否正確
    4. 成功呼叫：綜合評估是否完全成功（無異常 + 完美序列 + 完美引數）
    
    重要說明：
    - 異常呼叫會被排除在 IOU、LCS 和引數匹配的計算之外
    - 但 token 使用統計仍然包括所有呼叫（包括異常呼叫）
    - 所有指標都基於非異常呼叫進行計算
    
    引數:
        expected_calls: 期望的呼叫列表，格式為 [[模組名, 函式名, {引數字典}], ...]
            例如：[["MobilitySpace", "get_person", {"person_id": "@"}]]
        actual_calls: 實際的呼叫列表，格式為 [(模組名, 函式名, 引數字典), ...]
            應該已經排除了異常呼叫（通常透過 extract_call_signatures 函式獲得）
        tool_call_history: 完整的工具呼叫歷史記錄，包含異常資訊
            用於檢測是否有異常發生
        context: 上下文字典，包含 'id' 欄位（用於 '@' 標記的引數匹配）
            例如：{"id": 123}
    
    返回:
        Dict[str, Any]: 包含以下指標的字典：
            - tool_selection_iou (float): 工具選擇準確度，範圍 [0, 1]
            - sequence_lcs_score (float): 呼叫序列準確度，範圍 [0, 1]
            - param_accuracy (float): 引數準確度，範圍 [0, 1]
            - param_all_match (bool): 是否所有引數都完全匹配
            - has_exception (bool): 是否發生了異常
            - successful_call (bool): 是否完全成功（完美序列 + 完美引數）
            - expected_calls: 規範化後的期望呼叫列表
            - actual_calls: 規範化後的實際呼叫列表（僅非異常呼叫）
    """
    # 步驟1：規範化期望呼叫格式
    # 將期望呼叫轉換為統一的元組格式：(模組名, 函式名, 引數字典)
    expected_signatures = [
        (call[0], call[1], call[2] if isinstance(call[2], dict) else {}) for call in expected_calls
    ]
    # 實際呼叫應該已經排除了異常，直接使用
    actual_signatures = actual_calls

    # 步驟2：檢測是否有異常發生
    has_exception = any(
        call.get("exception_occurred", False) for call in (tool_call_history or [])
    )

    # ========== 指標1：工具選擇準確度（IOU） ==========
    # 該指標評估是否選擇了正確的工具函式，不考慮呼叫順序
    # 只比較函式名集合，不比較模組名和引數
    expected_function_names = {sig[1] for sig in expected_signatures}  # 提取期望的函式名集合
    actual_function_names = {sig[1] for sig in actual_signatures}  # 提取實際的函式名集合（已排除異常）
    tool_selection_iou = compute_iou(expected_function_names, actual_function_names)

    # ========== 指標2：呼叫序列準確度（LCS） ==========
    # 該指標評估呼叫順序是否正確
    # 只比較函式名序列，不比較模組名和引數
    expected_function_seq = [sig[1] for sig in expected_signatures]  # 提取期望的函式名序列
    actual_function_seq = [sig[1] for sig in actual_signatures]  # 提取實際的函式名序列（已排除異常）
    # 注意：LCS 分數 = LCS長度 / 期望序列長度
    # 這意味著如果實際序列包含了期望序列的所有元素（即使順序不完全相同），分數也可能很高
    sequence_lcs_score = compute_lcs_score(actual_function_seq, expected_function_seq)

    # ========== 指標3：引數準確度 ==========
    # 該指標評估每個函式呼叫的引數是否正確傳遞
    param_matches = []
    param_accuracies = []

    # 對每個期望呼叫，找到最佳匹配的實際呼叫
    for exp_module, exp_func, exp_kwargs in expected_signatures:
        best_match = None
        best_accuracy = 0.0

        # 遍歷所有實際呼叫，尋找匹配的呼叫
        for act_module, act_func, act_kwargs in actual_signatures:
            # 只有當模組名和函式名都匹配時，才進行引數匹配
            if exp_module == act_module and exp_func == act_func:
                all_match, accuracy = match_args(exp_kwargs, act_kwargs, context)
                if accuracy > best_accuracy:
                    best_accuracy = accuracy
                    best_match = all_match

        # 記錄匹配結果
        param_matches.append(best_match if best_match is not None else False)
        param_accuracies.append(best_accuracy)

    # 計算整體引數準確度
    if param_accuracies:
        param_accuracy = sum(param_accuracies) / len(param_accuracies)
        param_all_match = all(param_matches)
    else:
        # 如果沒有期望呼叫，根據是否有實際呼叫來判斷
        param_accuracy = 1.0 if not expected_signatures else 0.0
        param_all_match = not expected_signatures

    # ========== 指標4：成功呼叫 ==========
    # 完全成功需要：呼叫序列完全正確 + 所有引數都完全匹配
    successful_call = sequence_lcs_score == 1.0 and param_all_match

    return {
        "tool_selection_iou": tool_selection_iou,
        "sequence_lcs_score": sequence_lcs_score,
        "param_accuracy": param_accuracy,
        "param_all_match": param_all_match,
        "has_exception": has_exception,
        "successful_call": successful_call,
        "expected_calls": expected_signatures,
        "actual_calls": actual_signatures,
    }


def parse_instruction_types(yaml_data_path: str) -> List[str]:
    """
    從 YAML 檔案的註釋分段中解析指令型別（按順序返回）。

    規則：
    - 以註釋行 "# 1. xxx" / "# 2. xxx" 等作為型別標題
    - 每遇到一條 "- instruction:" 記錄當前型別
    """
    instruction_types: List[str] = []
    current_type = "未知型別"

    with open(yaml_data_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("#"):
                match = re.match(r"#\s*\d+\.\s*(.+)", stripped)
                if match:
                    current_type = match.group(1).strip()
                continue

            if stripped.startswith("- instruction:"):
                instruction_types.append(current_type)

    return instruction_types


async def initialize_environment(
    profiles_to_use: List[Dict],
    router_class,
    logger,
) -> Tuple[Any, List[int]]:
    """
    初始化環境模組和路由器。
    
    該函式建立測試所需的環境模組（MobilitySpace 和 EventSpace）和路由器例項，
    用於後續的指令測試。
    
    引數:
        profiles_to_use: 要使用的 agent profile 列表
        router_class: 路由器類（如 CodeGenRouter、ReActRouter 等）
        logger: 日誌記錄器
    
    返回:
        Tuple[Any, List[int]]: (環境路由器例項, Agent ID 列表)
    """
    START_TIME = datetime.now().replace(hour=7, minute=0, second=0, microsecond=0)

    # 建立移動性人員列表
    mobility_persons = []
    for profile in profiles_to_use:
        agent_id = profile["id"]
        mobility_persons.append(
            {
                "id": agent_id,
                "position": {
                    "kind": "aoi",
                    "aoi_id": profile["home"],
                },
            }
        )

    # 建立 MobilitySpace 環境
    home_dir = os.path.join(os.path.expanduser("~"), "agentsociety_data")
    map_path = os.path.join(home_dir, "beijing.pb")
    os.makedirs(home_dir, exist_ok=True)

    mobility_env = MobilitySpace(map_path, home_dir, persons=mobility_persons)
    # 定義允許的事件型別
    allowed_event_types = [
        "sleep",
        "home activity",
        "other",
        "work",
        "shopping",
        "eating out",
        "leisure and entertainment",
    ]
    event_space = EventSpace(allowed_event_types)

    # 建立社交媒體環境
    logger.info("\n【初始化社交媒體模組】")
    social_media_data_dir = os.getenv(
        "SOCIAL_MEDIA_DATA_DIR",
        os.path.join(os.path.expanduser("~/.agentsociety"), "social_media_data"),
    )
    logger.info(f"  ✓ 社交媒體資料目錄: {social_media_data_dir}")
    social_media_env = SocialMediaSpace(data_dir=social_media_data_dir)

    # 建立路由器（路由器將使用環境變數中的預設 LLM 配置）
    env_router = router_class(
        env_modules=[mobility_env, event_space, social_media_env]
    )
    await env_router.init(START_TIME)

    actual_agent_ids = [p["id"] for p in profiles_to_use]
    return env_router, actual_agent_ids


async def main(
    logger,
    router_class,
    yaml_data_path: str,
    num_agents: int = 10,
    profile_start_idx: int = 0,
):
    """
    執行指令測試基準評測，評估路由器的效能。
    
    該函式是評測系統的主入口，執行以下步驟：
    1. 載入 agent profiles
    2. 載入測試資料（YAML 格式）
    3. 初始化環境
    4. 執行所有測試用例並計算指標
    5. 統計結果並儲存
    
    引數:
        logger: 日誌記錄器
        router_class: 路由器類（如 CodeGenRouter、ReActRouter 等）
        yaml_data_path: 測試資料 YAML 檔案路徑
        num_agents: 使用的 agent 數量，預設為 10
        profile_start_idx: profile 的起始索引，預設為 0
    """
    logger.info("\n" + "=" * 80)
    logger.info("【Instruction Test Benchmark】")
    logger.info("=" * 80)
    logger.info(f"Router: {router_class.__name__}")
    logger.info(f"Test data: {yaml_data_path}")
    logger.info(f"Agent count: {num_agents}")
    # 在程式開始時輸出當前使用的 LLM 模型，便於後續排查與對比
    logger.info(f"Coder LLM model (CODER_MODEL_NAME): {CODER_MODEL_NAME}")
    print(f"Coder LLM model (CODER_MODEL_NAME): {CODER_MODEL_NAME}")
    logger.info("=" * 80)

    # ==================== Load Profiles ====================
    logger.info("\n【步驟1/4】載入 profiles.json...")
    profiles_path = os.path.join(os.path.dirname(__file__), "profiles.json")
    if not os.path.exists(profiles_path):
        logger.error(f"  ❌ profiles.json 檔案不存在: {profiles_path}")
        return

    with open(profiles_path, "r", encoding="utf-8") as f:
        profiles = json.load(f)

    logger.info(f"  ✓ 載入了 {len(profiles)} 個 agent profiles")

    if num_agents > len(profiles):
        logger.warning(
            f"  ⚠ 請求的 agent 數量 ({num_agents}) 超過 profiles 數量 ({len(profiles)})，使用全部 {len(profiles)} 個"
        )
        num_agents = len(profiles)

    profiles_to_use = profiles[profile_start_idx : profile_start_idx + num_agents]
    actual_agent_ids = [p["id"] for p in profiles_to_use]
    logger.info(f"  ✓ 實際 Agent IDs: {actual_agent_ids}")

    # ==================== Load YAML Test Data ====================
    logger.info("\n【步驟2/4】載入測試資料...")
    with open(yaml_data_path, "r", encoding="utf-8") as f:
        test_data = yaml.safe_load(f)

    instructions = test_data.get("instructions", [])
    instruction_types = parse_instruction_types(yaml_data_path)
    if instruction_types and len(instruction_types) == len(instructions):
        for idx, test_case in enumerate(instructions):
            test_case["instruction_type"] = instruction_types[idx]
    else:
        logger.warning(
            "  ⚠ 指令型別解析失敗或數量不匹配，按型別統計可能不準確"
        )
    logger.info(f"  ✓ 載入了 {len(instructions)} 條測試指令")

    # ==================== Initialize Environment ====================
    logger.info("\n【步驟3/4】初始化環境...")
    env_router, agent_ids = await initialize_environment(
        profiles_to_use, router_class, logger
    )
    logger.info(f"  ✓ 環境初始化完成，Agent IDs: {agent_ids}")

    # ==================== Run Tests ====================
    logger.info("\n【步驟4/4】執行測試...")
    
    # 定義用於迴圈的 agent ID 列表（1-5）
    context_agent_ids = [1, 2, 3, 4, 5]
    
    async def run_single_test(idx: int, test_case: Dict[str, Any]) -> Dict[str, Any]:
        """
        執行單個測試用例的輔助函式。
        
        引數:
            idx: 測試用例索引
            test_case: 測試用例字典
        
        返回:
            測試結果字典
        """
        instruction = test_case["instruction"]
        expected_calls = test_case.get("expected_calls", [])

        # 迴圈使用 agent ID（1-5）
        agent_id = context_agent_ids[idx % len(context_agent_ids)]
        context = {"id": agent_id}

        # 在每個測試前重置歷史記錄
        env_router.reset_tool_call_history()
        env_router.reset_token_usages()

        try:
            start_time = time.time()
            result, answer = await env_router.ask(context, instruction, readonly=False)
            end_time = time.time()
            duration = end_time - start_time

            # 獲取工具呼叫歷史
            tool_call_history = env_router.get_tool_call_history()
            # 提取呼叫簽名（排除異常呼叫）
            actual_calls = extract_call_signatures(tool_call_history, exclude_exceptions=True)

            # 獲取 token 使用統計（僅 coder 模型）
            token_usages = env_router.get_token_usages()
            coder_stats = token_usages.get("coder")
            total_llm_calls = coder_stats.call_count if coder_stats else 0
            total_input_tokens = coder_stats.input_tokens if coder_stats else 0
            total_output_tokens = coder_stats.output_tokens if coder_stats else 0

            # 計算指標
            metrics = compute_metrics(expected_calls, actual_calls, tool_call_history, context)

            return {
                "test_case": test_case,
                "context": context,
                "result": result,
                "answer": answer,
                "duration": duration,
                "metrics": metrics,
                "tool_call_history": tool_call_history,
                "token_usage": {
                    "total_llm_calls": total_llm_calls,
                    "total_input_tokens": total_input_tokens,
                    "total_output_tokens": total_output_tokens,
                    "total_tokens": total_input_tokens + total_output_tokens,
                    "by_model": {
                        model: {
                            "call_count": stats.call_count,
                            "input_tokens": stats.input_tokens,
                            "output_tokens": stats.output_tokens,
                        }
                        for model, stats in token_usages.items()
                    },
                },
            }
        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"  ❌ 測試用例 {idx+1} 失敗: {str(e)}")
            return {
                "test_case": test_case,
                "context": context,
                "error": str(e),
                "metrics": {
                    "tool_selection_iou": 0.0,
                    "sequence_lcs_score": 0.0,
                    "param_accuracy": 0.0,
                    "param_all_match": False,
                },
            }
    
    # 順序執行所有測試用例（避免並行導致的限流重試，減少LLM呼叫次數）
    results = []
    logger.info("  使用順序執行，避免限流重試")
    logger.info(f"  Agent ID 迴圈使用: {context_agent_ids}")
    
    for idx, test_case in enumerate(tqdm(instructions, desc="測試用例")):
        result = await run_single_test(idx, test_case)
        results.append(result)

    # ==================== 計算彙總統計 ====================
    logger.info("\n【結果統計】")

    # 分類結果：成功（無錯誤、無異常）vs 失敗（有錯誤或有異常）
    successful_results = [
        r for r in results
        if "error" not in r and not r.get("metrics", {}).get("has_exception", False)
    ]
    failed_results = [r for r in results if r not in successful_results]
    all_results = results  # 所有結果用於計算統計
    
    # 初始化統計變數（避免未定義錯誤）
    avg_tool_selection_iou = 0.0
    avg_sequence_lcs = 0.0
    avg_param_accuracy = 0.0
    successful_calls = 0
    successful_call_rate = 0.0
    total_llm_calls = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0
    avg_llm_calls_per_test = 0.0
    avg_input_tokens_per_test = 0.0
    avg_output_tokens_per_test = 0.0
    
    if all_results:
        n = len(all_results)
        # 計算平均指標（包括失敗的結果，因為 IOU 和 LCS 仍然有效）
        avg_tool_selection_iou = sum(r["metrics"]["tool_selection_iou"] for r in all_results) / n
        avg_sequence_lcs = sum(r["metrics"]["sequence_lcs_score"] for r in all_results) / n
        
        # 成功呼叫統計（無異常 + 完美 LCS + 完美引數）
        successful_calls = sum(1 for r in all_results if r["metrics"].get("successful_call", False))
        successful_call_rate = successful_calls / n
        
        # 引數準確度僅針對無異常的結果
        avg_param_accuracy = (
            sum(r["metrics"]["param_accuracy"] for r in successful_results) / len(successful_results)
            if successful_results else 0.0
        )
        
        # Token 使用統計（僅 coder 模型，包括所有結果，因為異常呼叫也消耗 token）
        total_llm_calls = sum(r.get("token_usage", {}).get("total_llm_calls", 0) for r in all_results)
        total_input_tokens = sum(r.get("token_usage", {}).get("total_input_tokens", 0) for r in all_results)
        total_output_tokens = sum(r.get("token_usage", {}).get("total_output_tokens", 0) for r in all_results)
        total_tokens = total_input_tokens + total_output_tokens
        avg_llm_calls_per_test = total_llm_calls / n
        avg_input_tokens_per_test = total_input_tokens / n
        avg_output_tokens_per_test = total_output_tokens / n

        logger.info(f"總測試用例數: {len(results)}")
        logger.info(f"無異常: {len(successful_results)}")
        logger.info(f"有異常: {len(failed_results)}")
        logger.info(f"成功呼叫（無異常+完美LCS+完美引數）: {successful_calls} ({successful_call_rate*100:.2f}%)")
        logger.info("\n平均指標（所有測試用例）:")
        logger.info(f"  工具選擇準確率 (IOU): {avg_tool_selection_iou:.4f}")
        logger.info(f"  呼叫序列準確率 (LCS): {avg_sequence_lcs:.4f}")
        if successful_results:
            logger.info(f"  引數準確率（僅無異常）: {avg_param_accuracy:.4f}")
        else:
            logger.info("  引數準確率（僅無異常）: N/A（所有測試用例都有異常）")
        logger.info("\nToken 使用統計（僅 coder 模型，包括所有結果，含異常呼叫）:")
        logger.info(f"  總 LLM 呼叫次數 (coder): {total_llm_calls}")
        logger.info(f"  平均每次測試 LLM 呼叫次數 (coder): {avg_llm_calls_per_test:.2f}")
        logger.info(f"  總 Input Tokens (coder): {total_input_tokens:,}")
        logger.info(f"  總 Output Tokens (coder): {total_output_tokens:,}")
        logger.info(f"  總 Tokens (coder): {total_tokens:,}")
        logger.info(f"  平均每次測試 Input Tokens (coder): {avg_input_tokens_per_test:,.0f}")
        logger.info(f"  平均每次測試 Output Tokens (coder): {avg_output_tokens_per_test:,.0f}")

    # ==================== 儲存結果 ====================
    output_path = f"logs_env/instruction_test_{router_class.__name__}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pkl"
    with open(output_path, "wb") as f:
        pickle.dump(results, f)
    logger.info(f"\n  ✓ 結果已儲存到: {output_path}")

    # 按型別統計正確呼叫（successful_call）
    type_stats: Dict[str, Dict[str, Any]] = {}
    for r in all_results:
        instruction_type = r.get("test_case", {}).get("instruction_type") or "未知型別"
        stats = type_stats.setdefault(
            instruction_type, {"count": 0, "successful_calls": 0, "success_rate": 0.0}
        )
        stats["count"] += 1
        if r.get("metrics", {}).get("successful_call", False):
            stats["successful_calls"] += 1

    for stats in type_stats.values():
        stats["success_rate"] = (
            stats["successful_calls"] / stats["count"] if stats["count"] else 0.0
        )

    if type_stats:
        logger.info("\n按型別統計正確呼叫（successful_call）:")
        for instruction_type, stats in type_stats.items():
            logger.info(
                f"  {instruction_type}: {stats['successful_calls']} / {stats['count']} "
                f"({stats['success_rate']*100:.2f}%)"
            )

    # 同時儲存摘要為 JSON 格式（彙總所有日誌中列印的統計資訊）
    summary = {
        "router": router_class.__name__,
        # 當前評測使用的 coder 模型名（集中定義在本檔案頂部的 CODER_MODEL_NAME 中）
        "current_coder_model_name": CODER_MODEL_NAME,
        "total_tests": len(results),
        "successful_tests": len(successful_results),
        "failed_tests": len(failed_results),
        "successful_calls": successful_calls,
        "successful_call_rate": successful_call_rate,
        "metrics": {
            "avg_tool_selection_iou": avg_tool_selection_iou,
            "avg_sequence_lcs": avg_sequence_lcs,
            "avg_param_accuracy": avg_param_accuracy,
        },
        "token_usage": {
            "total_llm_calls": total_llm_calls,
            "avg_llm_calls_per_test": avg_llm_calls_per_test,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
            "avg_input_tokens_per_test": avg_input_tokens_per_test,
            "avg_output_tokens_per_test": avg_output_tokens_per_test,
        },
        "by_type": type_stats,
    }

    summary_path = output_path.replace(".pkl", "_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info(f"  ✓ 摘要已儲存到: {summary_path}")

    # 僅為 CodeGenRouter 輸出每條指令的真值-生成結果對比
    if router_class is CodeGenRouter:
        comparison = []
        for idx, r in enumerate(results):
            test_case = r.get("test_case", {})
            metrics = r.get("metrics", {})
            comparison.append(
                {
                    "index": idx,
                    "instruction": test_case.get("instruction"),
                    "instruction_type": test_case.get("instruction_type"),
                    "expected_calls": test_case.get("expected_calls", []),
                    "actual_calls": metrics.get("actual_calls", [])
                }
            )

        compare_path = output_path.replace(".pkl", "_codegen_compare.json")
        with open(compare_path, "w", encoding="utf-8") as f:
            json.dump(comparison, f, indent=2, ensure_ascii=False)
        logger.info(f"  ✓ CodeGen 對比已儲存到: {compare_path}")


async def _main():
    setup_logging(
        log_file=f"logs_env/instruction_test_benchmark-{datetime.now().strftime('%Y%m%d%H%M%S')}.log",
        log_level=logging.INFO,
    )
    router_classes = {
        # "code_gen": CodeGenRouter,
        # "react": ReActRouter,
        # "plan_execute": PlanExecuteRouter,
        "search_tool": SearchToolRouter,
        "two_tier_react": TwoTierReActRouter,
        "two_tier_plan_execute": TwoTierPlanExecuteRouter,
    }

    yaml_data_path = os.path.join(os.path.dirname(__file__), "instructions_complete.yaml")

    for name, router_class in router_classes.items():
        logger = get_logger()
        logger.info(f"\n{'='*80}")
        logger.info(f"Testing router: {name} ({router_class.__name__})")
        logger.info(f"{'='*80}")

        # 為每個路由器初始化環境
        await main(
            logger,
            router_class,
            yaml_data_path=yaml_data_path,
            num_agents=10,
            profile_start_idx=0,
        )


if __name__ == "__main__":
    asyncio.run(_main())
