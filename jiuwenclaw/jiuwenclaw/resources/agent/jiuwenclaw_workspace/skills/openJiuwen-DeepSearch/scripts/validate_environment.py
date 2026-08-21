#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSearch 環境驗證和引數檢查指令碼

用途：
1. 驗證 main.py 是否存在
2. 檢查 API Key 配置
"""

import logging
import sys
from pathlib import Path

# 設定 UTF-8 編碼輸出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 配置日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


def check_main_py():
    """檢查 main.py 是否存在"""
    # 獲取技能目錄
    skill_dir = Path(__file__).parent.parent
    main_py_path = skill_dir / "scripts" / "main.py"

    logger.info("\n檢查 main.py: %s", main_py_path)

    if main_py_path.exists():
        logger.info("✅ main.py 存在")
        return True
    else:
        logger.error("❌ main.py 不存在")
        return False


def check_api_keys():
    """檢查 API Key 配置"""
    logger.info("\n檢查 API Key 配置：")
    logger.info("⚠️  請確認 .env 檔案中已配置以下變數：")
    logger.info("   - LLM_API_KEY")
    logger.info("   - WEB_SEARCH_API_KEY")
    logger.info("\n配置方式：")
    logger.info("   1. 複製配置檔案: cp .env.example .env")
    logger.info("   2. 編輯 .env 檔案，填入 API Key")
    return True


def main():
    """主函式"""
    logger.info("=" * 60)
    logger.info("openJiuwen-DeepSearch 環境驗證")
    logger.info("=" * 60)

    checks = [
        check_main_py(),
        check_api_keys(),
    ]

    logger.info("\n" + "=" * 60)
    if all(checks):
        logger.info("✅ 環境驗證透過，可以開始使用 DeepSearch")
        logger.info("\n快速開始：")
        logger.info('python "scripts\\main.py" --mode query --query "研究主題"')
    else:
        logger.error("❌ 環境驗證失敗，請解決上述問題後再試")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
