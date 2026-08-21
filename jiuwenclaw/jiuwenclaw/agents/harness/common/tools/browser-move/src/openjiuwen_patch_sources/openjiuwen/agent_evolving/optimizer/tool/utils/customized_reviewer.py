# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import re
import json
from typing import Callable, Optional, List

from openjiuwen.agent_evolving.optimizer.tool.utils.rits import get_rits_response


class ToolDescriptionReviewer:
    
    def __init__(self, eval_model_id: str, llm_api_key: str):
        self.eval_model_id = eval_model_id
        self.llm_api_key = llm_api_key
        self.processors: List[Callable] = []
    
    def format(self, json_schema: dict, description: str, example: Optional[str] = None) -> dict:
        
        prompt_original = f"""You will receive an input that contains a textual description.
The input may be free-form text, bullet points, or JSON in any structure.
Your task is to convert that content into MY target JSON format, while keeping the information and meaning exactly the same.

Do not add new information, remove information, or reinterpret ambiguous content.
Only reorganize and reformat the content according to the schema provided below.
Target JSON format: \n{json.dumps(json_schema, ensure_ascii=False, indent=2)}.

Output only valid JSON, following the exact structure of the target schema. Do not include explanations, comments, or additional text outside the JSON.

Now convert my following input to desired JSON format:
Input to be converted: \n{description}
"""
        prompt_1 = f"""You will receive an input that contains a textual description.
Your task is to convert it into the target JSON format below.

Rules:
- Preserve all information and meaning exactly.
- Do not add, remove, or reinterpret any information.
- You may rewrite and compress wording to eliminate redundancy.
- Do not restate information already implied by the schema (e.g. type=number/string, required fields).
- Enum/value lists must appear only once at the most relevant location; do not repeat them at field level.
- Use short, content-focused phrases for field descriptions.

Target JSON format:
{json.dumps(json_schema, ensure_ascii=False, indent=2)}

Output only valid JSON following the exact structure.
No explanations or extra text.

Input:
{description}
"""

        prompt_2 = f"""You will receive an input that contains a textual description.
Your task is to convert it into the target JSON format below.

Rules:
- Preserve all information and meaning exactly.
- Do not add, remove, or reinterpret any information.
- You may rewrite and compress wording to eliminate redundancy.
- Do not restate information already implied by the schema (e.g. field types, required fields).
- Do not describe required fields in natural language (e.g. phrases like “each item includes/contains …”).
- Enum/value lists must appear only once at the most relevant location.

Target JSON format:
{json.dumps(json_schema, ensure_ascii=False, indent=2)}

Output only valid JSON following the exact structure.
No explanations or extra text.

Input:
{description}
"""
        prompt = f"""將下面輸入轉換為目標 JSON 結構。必須滿足：

- 輸出只允許是有效 JSON，且嚴格匹配目標結構的鍵路徑與層級（不多不少）。
- 語義必須完全保留：不新增、不刪減、不改寫含義；可改寫措辭以壓縮。
- description 去冗餘是強制要求：
    - 任何 “每項包含/含有/由…組成/欄位包括…” 這類欄位清單式描述都必須刪除或改寫為非清單表述。
    - 不得在 description 中重複 schema 已表達的資訊：欄位名、欄位型別、required 已涵蓋的“必填”。
    - 僅保留 schema 無法表達或未顯式表達的約束到 description，例如：
        - 覆蓋區間/不得留隙/分段規則
        - 預設值語義（如 inflationRate 預設 0）
        - 業務規則（按年累加、考慮通脹等）
    - 列舉值列表只出現一次，放在最貼近欄位的位置（通常是該欄位的 description）；不得在父級/子級重複。
    如輸入中 description 同時包含“欄位清單 + 業務約束”，只保留業務約束部分。
    - 若某個 description 完全是冗餘欄位清單，允許變為簡短描述，但不得留空（除非輸入本身為空）。
- 請直接輸出轉換後的 JSON，不要附加解釋。

這是目標的json 模板:
{json.dumps(json_schema, ensure_ascii=False, indent=2)}

下面是你需要修改的json，生成後請自檢：所有 description 中不得出現“含/包含/包括/each item/contains/fields”等欄位列舉句式；否則重寫直到滿足。

Input:
{description}
"""
        
        def verify_output(output):
            return json.loads(output)
        
        response = get_rits_response(
            'gpt-5.2', 
            prompt, 
            self.llm_api_key, 
            verify_output=verify_output, 
            max_attempts=5, 
            include_stop_sequence=False,  
            verbose=False
        )
        return response
    
    @staticmethod
    def _is_mostly_english(text: str) -> bool:

        text_no_space = re.sub(r'\s+', '', text)
        
        if len(text_no_space) == 0:
            return False

        english_chars = len(re.findall(r'[a-zA-Z]', text_no_space))
        

        english_ratio = english_chars / len(text_no_space)
        

        return english_ratio > 0.7
    
    def clean_and_deduplicate(self, data: dict) -> dict:

        prompt = f"""
Given a tool description JSON, go through the content sentence 
by sentence and perform the following cleaning tasks:

1. Remove usage example in the main tool description
2. Remove redundant "必填"/"可選"/"required"/"optional" markers in parameter 
descriptions if they appear in 'required' session
3. Remove verbose, redundant descriptions including:
   - Disclaimers like "若輸入無效會返回空結果", 
    "若輸入程式碼無效或未收錄會返回未找到或空結果"
   - Obvious statements like "結果可能有延遲"
   - Suggestions like "呼叫者應自行進行進一步分析或合成總結", 
    "呼叫者應在本介面返回後自行進行進一步分析"
   - Irrelevant exclusions that are clearly not in the tool's 
    functional scope. e.g. the tool name is maps_directions, 
    since it's a direction tool, statements like "不提供預訂或支付功能" 
    or "不支援語音導航" is clearly irrelevant and need to be removed.
   - Any other unnecessary verbose content
4. Clean up descriptions: for parameter descriptions incorrectly 
mixed into the tool descriptions, relocate them to ensure that 
each parameter description is correctly placed in its corresponding 
parameter description instead of the main tool description session.

**Pay attention to KEEP statements on ACTUAL functionality boundaries**
Keep only unique, essential, and actionable information. Output only the 
cleaned JSON without explanations. DO NOT change the overall structure of JSON.

Input JSON:
{json.dumps(data, ensure_ascii=False, indent=2)}
"""
        
        def verify_output(output):
            return json.loads(output)
        
        response = get_rits_response(
            self.eval_model_id, 
            prompt, 
            self.llm_api_key, 
            verify_output=verify_output, 
            max_attempts=5, 
            include_stop_sequence=False,  
            verbose=False
        )
        return response
    
    def cross_check(self, data: dict, ori_tool: str):
        prompt = f"""比較原始描述和修改後的描述，按照以下要求整理修改後的描述：
1. 補充修改後的描述丟失的資訊：例如，引數可選值列表丟失，需把原始描述中的列表補充道修改後的對應位置。
2. 確保引數描述資訊和工具描述資訊位置正確：參考原始描述，確保工具描述中只包含對工具能力、邊界等資訊，確保引數具體細節要求應在對應的引數描述中，例如：“僅支援經緯度作為輸入”應當放在對應的引數描述中，不應當放在主工具能力邊界中。

確保不要改變json格式，僅修改文字內容。不要刪除內容，僅做整理和補充丟失資訊。

原始描述：
{ori_tool}

修改後描述（待最佳化）：
{json.dumps(data, ensure_ascii=False, indent=2)}
"""
        
        def verify_output(output):
            return json.loads(output)
        
        response = get_rits_response(
            self.eval_model_id, 
            prompt, 
            self.llm_api_key, 
            verify_output=verify_output, 
            max_attempts=5, 
            include_stop_sequence=False,  
            verbose=False
        )
        return response

    def translate_to_chinese(self, data: dict) -> dict:

        json_str = json.dumps(data, ensure_ascii=False)
        
        if not self._is_mostly_english(json_str):

            return data
        
        prompt = f"""Translate all English text in the following JSON to Chinese.
Keep JSON structure unchanged. Keep technical terms and code examples as-is.
Output only the translated JSON without explanations.

Input JSON:
{json.dumps(data, ensure_ascii=False, indent=2)}
"""
        
        def verify_output(output):
            return json.loads(output)
        
        response = get_rits_response(
            self.eval_model_id, 
            prompt, 
            self.llm_api_key, 
            verify_output=verify_output, 
            max_attempts=5, 
            include_stop_sequence=False,  
            verbose=False
        )
        return response

    def process(self, data: dict, ori_tool: str, steps: List[str]) -> dict:

        result = data
        
        for step in steps:
            if step == "cross_check":
                result = self.cross_check(data=data, ori_tool=ori_tool)
            elif step == "clean":
                result = self.clean_and_deduplicate(result)
            elif step == "translate":
                result = self.translate_to_chinese(result)
            else:
                raise ValueError(f"Unknown processing step: {step}")
        return result





    
