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
    检测文本是否包含中文字符

    Args:
        text: 待检测的文本

    Returns:
        如果包含中文字符返回True，否则返回False
    """
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            return True
    return False


async def translate_to_english(text: str, router: Router) -> str:
    """
    使用LLM将中文文本翻译成英文

    Args:
        text: 待翻译的中文文本
        router: LLM router实例

    Returns:
        翻译后的英文文本
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
        # 清理可能的额外格式
        translated = translated.strip()
        # 如果LLM返回了markdown格式，尝试提取纯文本
        if translated.startswith("```"):
            lines = translated.split("\n")
            translated = "\n".join(
                [line for line in lines if not line.strip().startswith("```")]
            )

        logger.info(f"翻译完成: '{text}' -> '{translated}'")
        return translated.strip()
    except Exception as e:
        logger.warning(f"翻译失败: {e}，将使用原文进行搜索")
        return text


def _split_query_by_keywords(query: str) -> List[str]:
    """
    基于关键词和连接词进行简单的查询拆分（备用方法）
    尽量保持原查询的短语结构

    Args:
        query: 原始查询文本

    Returns:
        拆分后的子主题列表
    """
    # 常见的连接词，按优先级排序
    # " and " 是最常见的，优先处理
    split_keywords = [" and ", " or ", " with ", " versus ", " vs ", " & "]

    # 尝试按连接词拆分
    for keyword in split_keywords:
        if keyword.lower() in query.lower():
            # 使用正则表达式进行不区分大小写的拆分
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            parts = pattern.split(query)
            # 清理每个部分
            parts = [p.strip() for p in parts if p.strip()]

            if len(parts) >= 2:
                # 验证每个部分至少 2 个单词
                valid_parts = []
                for part in parts:
                    word_count = len(part.split())
                    if word_count >= 2:
                        valid_parts.append(part)
                    else:
                        logger.debug(
                            f"关键词拆分：部分 '{part}' 太短（只有 {word_count} 个词），跳过"
                        )

                # 如果有效部分少于 2 个，返回原查询
                if len(valid_parts) < 2:
                    logger.info(f"关键词拆分后有效部分少于 2 个，使用原查询: '{query}'")
                    return [query]

                # 对于 "A and B" 模式，直接拆分为 ["A", "B"]
                # 例如："Complexity of social norms and cooperation mechanisms"
                # 拆分为：["Complexity of social norms", "cooperation mechanisms"]
                return valid_parts

    # 如果没有找到连接词，返回原查询
    return [query]


async def split_query_into_subtopics(query: str, router: Router) -> List[str]:
    """
    使用LLM将复杂查询拆分为多个子主题，尽量按照查询的字面意思拆分，不扩展原意

    Args:
        query: 原始查询文本
        router: LLM router实例

    Returns:
        子主题列表，如果拆分失败或只有一个主题，返回包含原查询的列表
    """
    # 首先尝试基于关键词的简单拆分（快速方法）
    keyword_split = _split_query_by_keywords(query)
    if len(keyword_split) >= 2:
        logger.info(f"使用关键词拆分: '{query}' -> {keyword_split}")
        return keyword_split

    # 检查查询是否太简单（单词数少于5个，可能无法拆分）
    word_count = len(query.split())
    if word_count < 5:
        logger.info(
            f"查询 '{query}' 太简单（{word_count} 个词），跳过拆分，使用单一查询"
        )
        return [query]

    # 如果简单拆分失败，使用LLM拆分
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

        # 尝试提取JSON数组
        # 移除可能的markdown代码块标记
        if result.startswith("```"):
            lines = result.split("\n")
            result = "\n".join(
                [line for line in lines if not line.strip().startswith("```")]
            )

        # 尝试解析JSON
        try:
            # 如果结果包含JSON，尝试提取
            json_match = re.search(r"\[.*?\]", result, re.DOTALL)
            if json_match:
                subtopics = json.loads(json_match.group())
            else:
                subtopics = json.loads(result)

            # 验证结果
            if isinstance(subtopics, list) and len(subtopics) >= 2:
                # 过滤空字符串和过短的主题
                # 每个子主题必须至少 2 个单词，且至少 3 个字符
                valid_subtopics = []
                for s in subtopics:
                    s = s.strip()
                    if not s:
                        continue
                    # 检查字符数
                    if len(s) < 3:
                        continue
                    # 检查单词数（至少 2 个单词）
                    word_count = len(s.split())
                    if word_count < 2:
                        logger.debug(
                            f"子主题 '{s}' 太短（只有 {word_count} 个词），跳过"
                        )
                        continue
                    valid_subtopics.append(s)

                # 如果有效子主题少于 2 个，说明拆分不合理，返回原查询
                if len(valid_subtopics) < 2:
                    logger.info(f"拆分后的有效子主题少于 2 个，使用原查询: '{query}'")
                    return [query]

                logger.info(f"查询拆分成功: '{query}' -> {valid_subtopics}")
                return valid_subtopics
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"解析子主题失败: {e}，将使用原查询")

        # 如果拆分失败，返回原查询
        logger.info(f"查询拆分失败或只有一个主题，使用原查询: '{query}'")
        return [query]
    except Exception as e:
        logger.warning(f"拆分查询失败: {e}，将使用原查询进行搜索")
        return [query]


def merge_literature_results(
    results: List[Dict[str, Any]], query: str
) -> Dict[str, Any]:
    """
    合并多个文献搜索结果，去重并合并

    Args:
        results: 多个搜索结果列表
        query: 原始查询

    Returns:
        合并后的文献搜索结果字典
    """
    if not results:
        return None

    # 使用标题和DOI作为唯一标识符进行去重
    seen_articles = {}
    all_articles = []

    for result in results:
        if not result or "articles" not in result:
            continue

        articles = result.get("articles", [])
        for article in articles:
            # 使用标题作为主要标识符
            title = article.get("title", "").strip().lower()
            doi = article.get("doi", "").strip().lower()

            # 创建唯一键
            if title:
                key = title
            elif doi:
                key = doi
            else:
                # 如果没有标题和DOI，使用其他字段
                key = str(hash(str(article)))

            # 如果文章已存在，合并chunks（保留相似度更高的）
            if key in seen_articles:
                existing_article = seen_articles[key]
                existing_chunks = existing_article.get("chunks", [])
                new_chunks = article.get("chunks", [])

                # 合并chunks，去重并保留相似度更高的
                chunk_map = {}
                for chunk in existing_chunks:
                    chunk_key = chunk.get("content", "")[
                        :100
                    ]  # 使用内容前100字符作为key
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
        f"合并搜索结果：从 {len(results)} 个查询结果中合并得到 {len(all_articles)} 篇唯一文献"
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
    调用文献搜索API获取相关文献信息

    Args:
        query: 搜索查询词（如果是中文，会自动翻译成英文）
        limit: 返回的文献数量
        router: LLM router实例（用于翻译和查询拆分，如果为None则使用默认router）
        year_from: 出版年份筛选（起始）
        year_to: 出版年份筛选（结束）
        sources: 指定数据源列表（默认为None，搜索全部数据源：local, arxiv, crossref, openalex）
        similarity_threshold: 本地搜索相似度阈值 (0.0-1.0)
        vector_similarity_weight: 向量权重 (0.0-1.0)
        chunk_content_limit: chunk内容长度限制
        relevant_content_limit: 相关内容长度限制
        max_chunks_per_article: 每篇文献的最大chunk数量
        return_chunks: 是否返回chunks
        enable_multi_query: 是否启用多查询模式，将复杂查询拆分为多个子主题分别搜索
        api_url: 文献搜索API的URL
        api_key: 文献搜索API的认证Key
        timeout: 请求超时时间（秒）

    Returns:
        文献搜索结果字典，如果失败返回None
    """
    # 如果router为None，使用默认router
    if router is None:
        router = get_llm_router("default")
    if not api_url:
        api_url = Config.get_literature_search_api_url()
    if not api_key:
        api_key = Config.get_literature_search_api_key()

    # 检测是否为中文，如果是则翻译成英文
    search_query = query
    if is_chinese_text(query):
        logger.info(f"检测到中文输入，正在翻译为英文: '{query}'")
        try:
            search_query = await translate_to_english(query, router)
            logger.info(f"翻译后的查询词: '{search_query}'")
        except Exception as e:
            logger.warning(f"翻译失败，将使用原文进行搜索: {e}")
            search_query = query

    # 多查询模式：将复杂查询拆分为多个子主题
    subtopics = [search_query]  # 默认使用原查询
    if enable_multi_query:
        logger.info(f"启用多查询模式，正在拆分查询: '{search_query}'")
        try:
            subtopics = await split_query_into_subtopics(search_query, router)
            if len(subtopics) > 1:
                logger.info(f"查询已拆分为 {len(subtopics)} 个子主题: {subtopics}")
            else:
                logger.info("查询无需拆分，使用单一查询")
        except Exception as e:
            logger.warning(f"拆分查询失败: {e}，将使用单一查询")
            subtopics = [search_query]

    # 如果只有一个子主题，使用单次查询
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

    # 多个子主题：并行搜索并合并结果
    logger.info(f"开始对 {len(subtopics)} 个子主题进行并行搜索...")
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

    # 过滤掉异常结果
    valid_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(f"子主题 '{subtopics[i]}' 搜索失败: {result}")
        elif result is not None:
            valid_results.append(result)

    if not valid_results:
        logger.warning("所有子主题搜索都失败")
        return None

    # 合并结果
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
    执行单次文献搜索（内部函数）

    Args:
        query: 搜索查询词
        limit: 返回的文献数量
        year_from: 出版年份筛选（起始）
        year_to: 出版年份筛选（结束）
        sources: 指定数据源列表
        similarity_threshold: 本地搜索相似度阈值
        vector_similarity_weight: 向量权重
        chunk_content_limit: chunk内容长度限制
        relevant_content_limit: 相关内容长度限制
        max_chunks_per_article: 每篇文献的最大chunk数量
        return_chunks: 是否返回chunks
        api_url: 文献搜索API的URL
        api_key: 文献搜索API的认证Key
        timeout: 请求超时时间（秒）

    Returns:
        文献搜索结果字典，如果失败返回None
    """
    try:
        async with aiohttp.ClientSession() as session:
            payload: Dict[str, Any] = {
                "query": query,
                "limit": limit,
                "return_chunks": return_chunks,
            }

            # 添加可选参数
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

            logger.debug(f"搜索请求参数: {payload}")

            async with session.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    # 转换响应格式以保持兼容性
                    converted_result = _convert_api_response(result, query)
                    total_articles = converted_result.get("total", 0)
                    logger.info(f"搜索成功，找到 {total_articles} 篇相关文献")
                    return converted_result
                elif response.status == 401:
                    logger.error("API认证失败，请检查 LITERATURE_SEARCH_API_KEY 配置")
                    return None
                else:
                    error_text = await response.text()
                    logger.warning(
                        f"搜索API返回错误状态码: {response.status}, {error_text}"
                    )
                    return None
    except asyncio.TimeoutError:
        logger.warning("搜索API请求超时")
        return None
    except Exception as e:
        logger.warning(f"搜索失败: {e}")
        return None


def _convert_api_response(response: Dict[str, Any], query: str) -> Dict[str, Any]:
    """
    将新API响应格式转换为内部格式

    新API返回 'results'，内部使用 'articles'
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

        # 处理 chunks 信息，统一字段名
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
                # 保留其他可能有用的字段
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
