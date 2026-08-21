"""Literature search core module

Core functions for searching academic literature using an external API.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Literal, Optional

import aiohttp
from agentsociety2.config import Config, get_llm_router
from agentsociety2.logger import get_logger
from litellm import AllMessageValues
from litellm.router import Router

logger = get_logger()


def is_chinese_text(text: str) -> bool:
    """
    檢測文字是否包含中文字元

    Args:
        text: 待檢測的文字

    Returns:
        如果包含中文字元返回True，否則返回False
    """
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            return True
    return False


async def translate_to_english(text: str, router: Router) -> str:
    """
    使用LLM將中文文字翻譯成英文

    Args:
        text: 待翻譯的中文文字
        router: LLM router例項

    Returns:
        翻譯後的英文文字
    """
    try:
        prompt = f"""Translate the following Chinese text directly to English. Only output the English translation with shortest words and no additional text.

Chinese text:
{text}

English translation:"""

        messages: List[AllMessageValues] = [{"role": "user", "content": prompt}]

        # Get model name from router
        model_name = router.model_list[0]["model_name"]
        response = await router.acompletion(
            model=model_name,
            messages=messages,
            stream=False,
        )

        translated = response.choices[0].message.content or text
        # 清理可能的額外格式
        translated = translated.strip()
        # 如果LLM返回了markdown格式，嘗試提取純文字
        if translated.startswith("```"):
            lines = translated.split("\n")
            translated = "\n".join(
                [line for line in lines if not line.strip().startswith("```")]
            )

        logger.info(f"翻譯完成: '{text}' -> '{translated}'")
        return translated.strip()
    except Exception as e:
        logger.warning(f"翻譯失敗: {e}，將使用原文進行搜尋")
        return text


def _split_query_by_keywords(query: str) -> List[str]:
    """
    基於關鍵詞和連線詞進行簡單的查詢拆分（備用方法）
    儘量保持原查詢的短語結構

    Args:
        query: 原始查詢文字

    Returns:
        拆分後的子主題列表
    """
    # 常見的連線詞，按優先順序排序
    # " and " 是最常見的，優先處理
    split_keywords = [" and ", " or ", " with ", " versus ", " vs ", " & "]

    # 嘗試按連線詞拆分
    for keyword in split_keywords:
        if keyword.lower() in query.lower():
            # 使用正規表示式進行不區分大小寫的拆分
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            parts = pattern.split(query)
            # 清理每個部分
            parts = [p.strip() for p in parts if p.strip()]

            if len(parts) >= 2:
                # 驗證每個部分至少 2 個單詞
                valid_parts = []
                for part in parts:
                    word_count = len(part.split())
                    if word_count >= 2:
                        valid_parts.append(part)
                    else:
                        logger.debug(
                            f"關鍵詞拆分：部分 '{part}' 太短（只有 {word_count} 個詞），跳過"
                        )

                # 如果有效部分少於 2 個，返回原查詢
                if len(valid_parts) < 2:
                    logger.info(f"關鍵詞拆分後有效部分少於 2 個，使用原查詢: '{query}'")
                    return [query]

                # 對於 "A and B" 模式，直接拆分為 ["A", "B"]
                # 例如："Complexity of social norms and cooperation mechanisms"
                # 拆分為：["Complexity of social norms", "cooperation mechanisms"]
                return valid_parts

    # 如果沒有找到連線詞，返回原查詢
    return [query]


async def split_query_into_subtopics(query: str, router: Router) -> List[str]:
    """
    使用LLM將複雜查詢拆分為多個子主題，儘量按照查詢的字面意思拆分，不擴充套件原意

    Args:
        query: 原始查詢文字
        router: LLM router例項

    Returns:
        子主題列表，如果拆分失敗或只有一個主題，返回包含原查詢的列表
    """
    # 首先嚐試基於關鍵詞的簡單拆分（快速方法）
    keyword_split = _split_query_by_keywords(query)
    if len(keyword_split) >= 2:
        logger.info(f"使用關鍵詞拆分: '{query}' -> {keyword_split}")
        return keyword_split

    # 檢查查詢是否太簡單（單詞數少於5個，可能無法拆分）
    word_count = len(query.split())
    if word_count < 5:
        logger.info(
            f"查詢 '{query}' 太簡單（{word_count} 個詞），跳過拆分，使用單一查詢"
        )
        return [query]

    # 如果簡單拆分失敗，使用LLM拆分
    try:
        prompt = f"""Split the following research query into 2-4 subtopics by directly extracting key phrases from the original query. DO NOT expand or rephrase the meaning. Use the exact words and phrases from the query.

Query: {query}

Rules:
1. Extract key phrases directly from the query, keeping the original wording
2. Split by conjunctions (and, or, with, etc.) or natural phrase boundaries
3. DO NOT add new concepts or expand the meaning
4. Each subtopic MUST be a meaningful phrase with at least 2 words (e.g., "social norms", "cooperation mechanisms")
5. DO NOT create subtopics with only a single word (e.g., "complexity", "mechanisms" alone are NOT valid)
6. If the query is too simple and cannot be split into at least 2 meaningful multi-word phrases, return the original query as a single-item array

Please output ONLY a JSON array of subtopics, with no additional text.

Subtopic array:"""

        messages: List[AllMessageValues] = [{"role": "user", "content": prompt}]

        # Get model name from router
        model_name = router.model_list[0]["model_name"]
        response = await router.acompletion(
            model=model_name,
            messages=messages,
            stream=False,
        )

        result = response.choices[0].message.content or ""
        result = result.strip()

        # 嘗試提取JSON陣列
        # 移除可能的markdown程式碼塊標記
        if result.startswith("```"):
            lines = result.split("\n")
            result = "\n".join(
                [line for line in lines if not line.strip().startswith("```")]
            )

        # 嘗試解析JSON
        try:
            # 如果結果包含JSON，嘗試提取
            json_match = re.search(r"\[.*?\]", result, re.DOTALL)
            if json_match:
                subtopics = json.loads(json_match.group())
            else:
                subtopics = json.loads(result)

            # 驗證結果
            if isinstance(subtopics, list) and len(subtopics) >= 2:
                # 過濾空字串和過短的主題
                # 每個子主題必須至少 2 個單詞，且至少 3 個字元
                valid_subtopics = []
                for s in subtopics:
                    s = s.strip()
                    if not s:
                        continue
                    # 檢查字元數
                    if len(s) < 3:
                        continue
                    # 檢查單詞數（至少 2 個單詞）
                    word_count = len(s.split())
                    if word_count < 2:
                        logger.debug(
                            f"子主題 '{s}' 太短（只有 {word_count} 個詞），跳過"
                        )
                        continue
                    valid_subtopics.append(s)

                # 如果有效子主題少於 2 個，說明拆分不合理，返回原查詢
                if len(valid_subtopics) < 2:
                    logger.info(f"拆分後的有效子主題少於 2 個，使用原查詢: '{query}'")
                    return [query]

                logger.info(f"查詢拆分成功: '{query}' -> {valid_subtopics}")
                return valid_subtopics
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"解析子主題失敗: {e}，將使用原查詢")

        # 如果拆分失敗，返回原查詢
        logger.info(f"查詢拆分失敗或只有一個主題，使用原查詢: '{query}'")
        return [query]
    except Exception as e:
        logger.warning(f"拆分查詢失敗: {e}，將使用原查詢進行搜尋")
        return [query]


def merge_literature_results(
    results: List[Dict[str, Any]], query: str
) -> Dict[str, Any]:
    """
    合併多個文獻搜尋結果，去重併合並

    Args:
        results: 多個搜尋結果列表
        query: 原始查詢

    Returns:
        合併後的文獻搜尋結果字典
    """
    if not results:
        return None

    # 使用標題和DOI作為唯一識別符號進行去重
    seen_articles = {}
    all_articles = []

    for result in results:
        if not result or "articles" not in result:
            continue

        articles = result.get("articles", [])
        for article in articles:
            # 使用標題作為主要識別符號
            title = article.get("title", "").strip().lower()
            doi = article.get("doi", "").strip().lower()

            # 建立唯一鍵
            if title:
                key = title
            elif doi:
                key = doi
            else:
                # 如果沒有標題和DOI，使用其他欄位
                key = str(hash(str(article)))

            # 如果文章已存在，合併chunks（保留相似度更高的）
            if key in seen_articles:
                existing_article = seen_articles[key]
                existing_chunks = existing_article.get("chunks", [])
                new_chunks = article.get("chunks", [])

                # 合併chunks，去重並保留相似度更高的
                chunk_map = {}
                for chunk in existing_chunks:
                    chunk_key = chunk.get("content", "")[
                        :100
                    ]  # 使用內容前100字元作為key
                    if chunk_key:
                        chunk_map[chunk_key] = chunk

                for chunk in new_chunks:
                    chunk_key = chunk.get("content", "")[:100]
                    if chunk_key:
                        if chunk_key not in chunk_map:
                            chunk_map[chunk_key] = chunk
                        else:
                            # 保留相似度更高的chunk
                            existing_sim = chunk_map[chunk_key].get("similarity") or 0
                            new_sim = chunk.get("similarity") or 0
                            if new_sim > existing_sim:
                                chunk_map[chunk_key] = chunk

                existing_article["chunks"] = list(chunk_map.values())
                # 更新平均相似度
                if existing_article["chunks"]:
                    avg_sim = sum(
                        c.get("similarity") or 0 for c in existing_article["chunks"]
                    ) / len(existing_article["chunks"])
                    existing_article["avg_similarity"] = avg_sim
            else:
                seen_articles[key] = article.copy()
                all_articles.append(seen_articles[key])

    if not all_articles:
        return None

    # 按平均相似度排序
    all_articles.sort(key=lambda x: x.get("avg_similarity") or 0, reverse=True)

    logger.info(
        f"合併搜尋結果：從 {len(results)} 個查詢結果中合併得到 {len(all_articles)} 篇唯一文獻"
    )

    return {"articles": all_articles, "total": len(all_articles), "query": query}


async def search_literature(
    query: str,
    limit: int = 10,
    router: Optional[Router] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    sources: Optional[List[Literal["local", "arxiv", "crossref", "openalex"]]] = None,
    similarity_threshold: Optional[float] = None,
    vector_similarity_weight: Optional[float] = None,
    chunk_content_limit: Optional[int] = None,
    relevant_content_limit: Optional[int] = None,
    max_chunks_per_article: Optional[int] = None,
    return_chunks: bool = True,
    enable_multi_query: bool = False,
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: int = 120,
) -> Optional[Dict[str, Any]]:
    """
    呼叫文獻搜尋API獲取相關文獻資訊

    Args:
        query: 搜尋查詢詞（如果是中文，會自動翻譯成英文）
        limit: 返回的文獻數量
        router: LLM router例項（用於翻譯和查詢拆分，如果為None則使用預設router）
        year_from: 出版年份篩選（起始）
        year_to: 出版年份篩選（結束）
        sources: 指定資料來源列表（預設為None，搜尋全部資料來源：local, arxiv, crossref, openalex）
        similarity_threshold: 本地搜尋相似度閾值 (0.0-1.0)
        vector_similarity_weight: 向量權重 (0.0-1.0)
        chunk_content_limit: chunk內容長度限制
        relevant_content_limit: 相關內容長度限制
        max_chunks_per_article: 每篇文獻的最大chunk數量
        return_chunks: 是否返回chunks
        enable_multi_query: 是否啟用多查詢模式，將複雜查詢拆分為多個子主題分別搜尋
        api_url: 文獻搜尋API的URL
        api_key: 文獻搜尋API的認證Key
        timeout: 請求超時時間（秒）

    Returns:
        文獻搜尋結果字典，如果失敗返回None
    """
    # 如果router為None，使用預設router
    if router is None:
        router = get_llm_router("default")
    if not api_url:
        api_url = Config.get_literature_search_api_url()
    if not api_key:
        api_key = Config.get_literature_search_api_key()

    # 檢測是否為中文，如果是則翻譯成英文
    search_query = query
    if is_chinese_text(query):
        logger.info(f"檢測到中文輸入，正在翻譯為英文: '{query}'")
        try:
            search_query = await translate_to_english(query, router)
            logger.info(f"翻譯後的查詢詞: '{search_query}'")
        except Exception as e:
            logger.warning(f"翻譯失敗，將使用原文進行搜尋: {e}")
            search_query = query

    # 多查詢模式：將複雜查詢拆分為多個子主題
    subtopics = [search_query]  # 預設使用原查詢
    if enable_multi_query:
        logger.info(f"啟用多查詢模式，正在拆分查詢: '{search_query}'")
        try:
            subtopics = await split_query_into_subtopics(search_query, router)
            if len(subtopics) > 1:
                logger.info(f"查詢已拆分為 {len(subtopics)} 個子主題: {subtopics}")
            else:
                logger.info("查詢無需拆分，使用單一查詢")
        except Exception as e:
            logger.warning(f"拆分查詢失敗: {e}，將使用單一查詢")
            subtopics = [search_query]

    # 如果只有一個子主題，使用單次查詢
    if len(subtopics) == 1:
        return await _search_literature_single(
            query=subtopics[0],
            limit=limit,
            year_from=year_from,
            year_to=year_to,
            sources=sources,
            similarity_threshold=similarity_threshold,
            vector_similarity_weight=vector_similarity_weight,
            chunk_content_limit=chunk_content_limit,
            relevant_content_limit=relevant_content_limit,
            max_chunks_per_article=max_chunks_per_article,
            return_chunks=return_chunks,
            api_url=api_url,
            api_key=api_key,
            timeout=timeout,
        )

    # 多個子主題：並行搜尋併合並結果
    logger.info(f"開始對 {len(subtopics)} 個子主題進行並行搜尋...")
    search_tasks = [
        _search_literature_single(
            query=subtopic,
            limit=limit,
            year_from=year_from,
            year_to=year_to,
            sources=sources,
            similarity_threshold=similarity_threshold,
            vector_similarity_weight=vector_similarity_weight,
            chunk_content_limit=chunk_content_limit,
            relevant_content_limit=relevant_content_limit,
            max_chunks_per_article=max_chunks_per_article,
            return_chunks=return_chunks,
            api_url=api_url,
            api_key=api_key,
            timeout=timeout,
        )
        for subtopic in subtopics
    ]

    results = await asyncio.gather(*search_tasks, return_exceptions=True)

    # 過濾掉異常結果
    valid_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(f"子主題 '{subtopics[i]}' 搜尋失敗: {result}")
        elif result is not None:
            valid_results.append(result)

    if not valid_results:
        logger.warning("所有子主題搜尋都失敗")
        return None

    # 合併結果
    return merge_literature_results(valid_results, search_query)


async def _search_literature_single(
    query: str,
    limit: int,
    year_from: Optional[int],
    year_to: Optional[int],
    sources: Optional[List[str]],
    similarity_threshold: Optional[float],
    vector_similarity_weight: Optional[float],
    chunk_content_limit: Optional[int],
    relevant_content_limit: Optional[int],
    max_chunks_per_article: Optional[int],
    return_chunks: bool,
    api_url: str,
    api_key: str,
    timeout: int,
) -> Optional[Dict[str, Any]]:
    """
    執行單次文獻搜尋（內部函式）

    Args:
        query: 搜尋查詢詞
        limit: 返回的文獻數量
        year_from: 出版年份篩選（起始）
        year_to: 出版年份篩選（結束）
        sources: 指定資料來源列表
        similarity_threshold: 本地搜尋相似度閾值
        vector_similarity_weight: 向量權重
        chunk_content_limit: chunk內容長度限制
        relevant_content_limit: 相關內容長度限制
        max_chunks_per_article: 每篇文獻的最大chunk數量
        return_chunks: 是否返回chunks
        api_url: 文獻搜尋API的URL
        api_key: 文獻搜尋API的認證Key
        timeout: 請求超時時間（秒）

    Returns:
        文獻搜尋結果字典，如果失敗返回None
    """
    try:
        async with aiohttp.ClientSession() as session:
            payload: Dict[str, Any] = {
                "query": query,
                "limit": limit,
                "return_chunks": return_chunks,
            }

            # 新增可選引數
            if year_from is not None:
                payload["year_from"] = year_from
            if year_to is not None:
                payload["year_to"] = year_to
            if sources is not None:
                payload["sources"] = sources
            if similarity_threshold is not None:
                payload["similarity_threshold"] = similarity_threshold
            if vector_similarity_weight is not None:
                payload["vector_similarity_weight"] = vector_similarity_weight
            if chunk_content_limit is not None:
                payload["chunk_content_limit"] = chunk_content_limit
            if relevant_content_limit is not None:
                payload["relevant_content_limit"] = relevant_content_limit
            if max_chunks_per_article is not None:
                payload["max_chunks_per_article"] = max_chunks_per_article

            headers = {
                "Content-Type": "application/json",
            }
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            logger.debug(f"搜尋請求引數: {payload}")

            async with session.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    # 轉換響應格式以保持相容性
                    converted_result = _convert_api_response(result, query)
                    total_articles = converted_result.get("total", 0)
                    logger.info(f"搜尋成功，找到 {total_articles} 篇相關文獻")
                    return converted_result
                elif response.status == 401:
                    logger.error("API認證失敗，請檢查 LITERATURE_SEARCH_API_KEY 配置")
                    return None
                else:
                    error_text = await response.text()
                    logger.warning(
                        f"搜尋API返回錯誤狀態碼: {response.status}, {error_text}"
                    )
                    return None
    except asyncio.TimeoutError:
        logger.warning("搜尋API請求超時")
        return None
    except Exception as e:
        logger.warning(f"搜尋失敗: {e}")
        return None


def _convert_api_response(response: Dict[str, Any], query: str) -> Dict[str, Any]:
    """
    將新API響應格式轉換為內部格式

    新API返回 'results'，內部使用 'articles'
    """
    results = response.get("results", [])
    articles = []

    for item in results:
        article = {
            "title": item.get("title", "Unknown Title"),
            "abstract": item.get("abstract", ""),
            "journal": item.get("journal", ""),
            "doi": item.get("doi", ""),
            "url": item.get("url", ""),
            "year": item.get("year"),
            "authors": item.get("authors", []),
            "avg_similarity": item.get("score", 0) or item.get("avg_similarity", 0),
            "source": item.get("source", ""),
            "source_name": item.get("source_name", ""),
        }

        # 處理 chunks 資訊，統一欄位名
        chunks = item.get("chunks", [])
        if chunks:
            converted_chunks = []
            for chunk in chunks:
                converted_chunk = {
                    "content": chunk.get("content", "")
                    or chunk.get("relevant_content", ""),
                    "similarity": chunk.get("similarity_score", 0)
                    or chunk.get("similarity", 0),
                    "chunk_id": chunk.get("chunk_id", ""),
                    "document_id": chunk.get("document_id", ""),
                }
                # 保留其他可能有用的欄位
                if chunk.get("vector_similarity"):
                    converted_chunk["vector_similarity"] = chunk["vector_similarity"]
                if chunk.get("term_similarity"):
                    converted_chunk["term_similarity"] = chunk["term_similarity"]
                converted_chunks.append(converted_chunk)
            article["chunks"] = converted_chunks

        articles.append(article)

    return {
        "articles": articles,
        "total": response.get("total", len(articles)),
        "query": query,
        "sources": response.get("sources", {}),
    }
