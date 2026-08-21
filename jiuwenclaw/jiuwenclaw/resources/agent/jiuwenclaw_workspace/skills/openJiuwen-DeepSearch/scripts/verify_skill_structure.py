"""
技能結構驗證指令碼

驗證 openJariwen-DeepSearch 技能是否符合 OpenClaw/Claude Code 技能標準
"""
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def check_skill_structure(skill_root: Path) -> bool:
    """
    檢查技能結構

    Args:
        skill_root: 技能根目錄

    Returns:
        所有檢查是否透過
    """
    logger.info("Checking skill directory: %s\n", skill_root)

    all_passed = True

    # 檢查必需檔案
    required_files = [
        "SKILL.md",
        "README.md",
        "pyproject.toml",
        ".env.example",
        "scripts/main.py",
        "scripts/agent_entry.py",
    ]

    logger.info("Checking required files:")
    for file_path in required_files:
        full_path = skill_root / file_path
        if full_path.exists():
            logger.info("  [OK] %s", file_path)
        else:
            logger.error("  [MISSING] %s", file_path)
            all_passed = False

    # 檢查 SKILL.md 格式
    logger.info("\nChecking SKILL.md format:")
    skill_md = skill_root / "SKILL.md"
    if skill_md.exists():
        content = skill_md.read_text(encoding="utf-8")
        if content.startswith("---"):
            logger.info("  [OK] YAML frontmatter exists")
            if "name:" in content:
                logger.info("  [OK] name field exists")
            else:
                logger.error("  [MISSING] name field")
                all_passed = False
            if "description:" in content:
                logger.info("  [OK] description field exists")
            else:
                logger.error("  [MISSING] description field")
                all_passed = False
        else:
            logger.error("  [MISSING] YAML frontmatter")
            all_passed = False
    else:
        logger.error("  [MISSING] SKILL.md")
        all_passed = False

    # 檢查指令碼中的路徑硬編碼
    logger.info("\nChecking for hardcoded paths in scripts:")
    main_py = skill_root / "scripts" / "main.py"
    if main_py.exists():
        content = main_py.read_text(encoding="utf-8")

        # 檢查是否使用了 SKILL_ROOT
        if "SKILL_ROOT" in content:
            logger.info("  [OK] main.py uses SKILL_ROOT")
        else:
            logger.error("  [FAIL] main.py does not use SKILL_ROOT")
            all_passed = False

        # 檢查是否有硬編碼的相對路徑
        if '"./output/logs"' in content or '"./output/results"' in content:
            logger.error("  [FAIL] main.py has hardcoded paths")
            all_passed = False
        else:
            logger.info("  [OK] main.py has no hardcoded paths")

    # 檢查 agent_entry.py
    logger.info("\nChecking agent_entry.py:")
    agent_entry = skill_root / "scripts" / "agent_entry.py"
    if agent_entry.exists():
        content = agent_entry.read_text(encoding="utf-8")

        if "def deep_search" in content:
            logger.info("  [OK] deep_search function exists")
        else:
            logger.error("  [MISSING] deep_search function")
            all_passed = False

        if "SKILL_ROOT" in content:
            logger.info("  [OK] agent_entry.py uses SKILL_ROOT")
        else:
            logger.error("  [FAIL] agent_entry.py does not use SKILL_ROOT")
            all_passed = False
    else:
        logger.error("  [MISSING] agent_entry.py")
        all_passed = False

    # 檢查 pyproject.toml
    logger.info("\nChecking pyproject.toml:")
    pyproject = skill_root / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text(encoding="utf-8")

        if "requires-python" in content:
            logger.info("  [OK] requires-python exists")
            if "3.11" in content:
                logger.info("  [OK] Python version requirement is 3.11+")
            else:
                logger.warning("  [WARNING] Python version requirement may be incorrect")
        else:
            logger.error("  [MISSING] requires-python")
            all_passed = False

        if "openjiuwen-deepsearch" in content:
            logger.info("  [OK] Dependencies configured correctly")
        else:
            logger.error("  [MISSING] Dependencies configuration")
            all_passed = False
    else:
        logger.error("  [MISSING] pyproject.toml")
        all_passed = False

    return all_passed


def main():
    """主函式"""
    # 配置日誌輸出到控制檯
    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s',
        handlers=[logging.StreamHandler()]
    )

    # 獲取技能根目錄
    script_path = Path(__file__).resolve()
    skill_root = script_path.parent.parent

    logger.info("=" * 80)
    logger.info("openJiuwen-DeepSearch Skill Structure Verification")
    logger.info("=" * 80)
    logger.info("")

    # 檢查技能結構
    all_passed = check_skill_structure(skill_root)

    logger.info("")
    logger.info("=" * 80)
    if all_passed:
        logger.info("[SUCCESS] All checks passed! Skill meets OpenClaw/Claude Code standards.")
        logger.info("=" * 80)
        return 0
    else:
        logger.error("[FAILURE] Some checks failed. Please fix issues above.")
        logger.info("=" * 80)
        return 1


if __name__ == "__main__":
    sys.exit(main())
