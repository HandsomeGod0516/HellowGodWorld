# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
import json
import asyncio
from typing import Any, Dict, Optional, Callable

from fastmcp.client import Client
from fastmcp.client import SSETransport


def make_sync_mcp_caller(
    url: str,
    name: str = "Streamable HTTP Python Server", 
) -> Callable[[Dict[str, Any]], Any]:

    def call(tool_arguments: Dict[str, Any]) -> Any:
        async def _run():
            transport = SSETransport(url=url)
            client = Client(transport)

            async with client:  
                tool_name = tool_arguments["name"]
                arguments = tool_arguments.get("arguments")

                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError as e:
                        raise ValueError(
                            "Failed to parse `arguments` as JSON string. "
                            f"Raw arguments: {arguments}"
                        ) from e

                result = await client.call_tool(tool_name, arguments)
                return result.content[0].text

        return asyncio.run(_run())

    return call


MCP_URL = os.getenv("MCP_URL", "")
MCP_NAME = os.getenv("MCP_NAME", "Streamable HTTP Python Server")


gaode_map_mcp_generic = make_sync_mcp_caller(MCP_URL)

schema = {
    "type": "function",
    "function": {
        "name": "SearchFunds",
        "description": """搜尋基金、根據基金名稱匹配基金程式碼。
透過名稱（可用於確定基金程式碼）、程式碼、拼音、交易狀態等資訊進行搜尋。
同時可以按照收益、限額、費率等進行排序，，在大部分情況都需要此工具。
（注意如果使用了keyword，就不要使用“分類”這個引數，
另外returnYear指的是近一年收益）""".strip(),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": """分類 (可選值: '', '不限', '
偏股型', '指數型', 
'QDII型', '商品型', '債券型', '貨幣型', 
'國企改革', '工業4.0', '國防軍工', 
'城鎮化', '消費', '節能環保', '美麗中國',
 '養老', '價值藍籌', '金融', '一帶一路', 
'農林牧漁', '資源', 'TMT', '新能源', 
'文化傳媒', '健康中國', '新興產業', '量化投資', '定增', 
'逆向投資', '滬港深', '量化對沖', '打新', 
'股票型', '偏股混合型', '平衡混合型', '靈活配置型', 
'偏債混合型', '綜合指數', '規模指數', '策略指數', 
'風格指數', '行業主題指數', '定製指數', '債券指數', 
'國際股票型', '國際混合型', '國際債券型', 
'國際另類投資', '全球市場', '美國市場', '歐洲市場', 
'香港市場', '亞太市場', '新興市場', '大中華市場', 
'黃金', '白銀', '油氣', '純債', '一級債', 
'二級債', '高槓杆', '利率債', '信用債', 
'可轉債', '偏股債')""".strip()
                },
                "keyword": {
                    "type": "string",
                    "description": "基金名稱關鍵字，支援分詞搜尋"
                },
                "size": {
                    "type": "number",
                    "description": "每頁數量"
                },
                "sortOrder": {
                    "type": "string",
                    "description": "選擇排序的順序，如果是查詢最大、最多等，可以是\"降序\"，否則為\"升序\" (可選值: '', '升序', '降序')"
                },
                "tradeStatus": {
                    "type": "string",
                    "description": "交易狀態 (可選值: '', '不限', '正常開放', '認購期', '暫停申購', '暫停贖回', '暫停交易')"
                },
                "sortColumn": {
                    "type": "string",
                    "description": "選擇要排序的列，可選值：成立日期、基金規模、收益率、近一年收益、起購金額、基金限額、選股能力、擇時能力、最新股票倉位、綜合費率、跟蹤誤差、七日年化收益率、萬份收益"
                },
                "page": {
                    "type": "number",
                    "description": "頁碼，從0開始"
                }
            }
        }
    }
}
description = json.dumps(schema, ensure_ascii=False)
tool = {'name': 'SearchFunds', 'description': description}
