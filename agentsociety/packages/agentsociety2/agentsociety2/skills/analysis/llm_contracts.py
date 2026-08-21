"""
分析模块与 LLM 交互的 **输出契约**（XML 片段与说明函数）。

可组合的 **自然语言能力说明** 见 `instruction_md/`（`utils.get_analysis_skills`）；模块总览见 `README.md`。
"""

# 通用裁判 XML 格式（分析/策略/可视化/报告等判断）
JUDGMENT_XML = (
    "<judgment><success>true</success><reason>...</reason>"
    "<should_retry>false</should_retry><retry_instruction>...</retry_instruction></judgment>"
)

# 报告生成 XML 格式（中英双语各一份 Markdown + HTML，图表路径与 assets 引用保持一致）
REPORT_XML = (
    "<report>"
    "<markdown_zh><![CDATA[Chinese Markdown]]></markdown_zh>"
    "<html_zh><![CDATA[Chinese full HTML document]]></html_zh>"
    "<markdown_en><![CDATA[English Markdown]]></markdown_en>"
    "<html_en><![CDATA[English full HTML document]]></html_en>"
    "</report>"
)

# 报告裁判 XML
REPORT_JUDGMENT_XML = (
    "<judgment><success>true</success><reason>...</reason>"
    "<has_markdown>true</has_markdown><has_html>true</has_html>"
    "<should_retry>false</should_retry><retry_instruction>...</retry_instruction></judgment>"
)

# 上下文摘要 XML 格式
SUMMARY_XML = (
    "<summary><key_findings><item>...</item></key_findings>"
    "<failed_attempts><item>...</item></failed_attempts>"
    "<successful_tools><item>...</item></successful_tools>"
    "<recommendations>...</recommendations></summary>"
)


def judgment_prompt(suffix: str = "") -> str:
    """返回裁判类 prompt 的 XML 要求部分。"""
    return f"Return only XML: {JUDGMENT_XML}{suffix}"


def report_xml_instruction() -> str:
    """返回报告生成的 XML 要求。"""
    return (
        f"**Must** return only XML: {REPORT_XML} "
        "Chinese sections use professional 简体中文; English sections are full English. "
        "Both locales must embed the same charts using the same relative paths "
        '(e.g. `assets/file.png`).'
    )


def report_judgment_prompt() -> str:
    """返回报告裁判的 XML 要求。"""
    return f"Return only XML: {REPORT_JUDGMENT_XML}"


def summary_xml_contract() -> str:
    """返回上下文摘要的 XML 要求。"""
    return f"Return only XML: {SUMMARY_XML}"


def analysis_xml_contract() -> str:
    """分析结果生成的 XML 约定。"""
    return """Return only XML:
<analysis>
  <insights><item>...</item><item>...</item></insights>
  <findings><item>...</item></findings>
  <conclusions>...</conclusions>
  <recommendations><item>...</item></recommendations>
</analysis>"""


def strategy_xml_contract() -> str:
    """分析策略生成的 XML 约定。"""
    return """Return only XML:
<strategy>
  <analysis_strategy>...</analysis_strategy>
  <tools_to_use>
    <tool><tool_name>...</tool_name><tool_type>code_executor|eda_profile|eda_sweetviz|read_file|write_file|list_directory|glob|search_file_content|literature_search|load_literature|write_todos|run_shell_command</tool_type><action>...</action><parameters>{{}}</parameters></tool>
  </tools_to_use>
</strategy>

Available tool types (use based on analysis needs):
- code_executor: Run Python code for custom analysis/visualization
- eda_profile: Generate ydata-profiling EDA report
- eda_sweetviz: Generate Sweetviz EDA report
- read_file: Read file contents (for artifacts, logs)
- write_file: Write content to file
- list_directory: List directory contents
- glob: Find files matching pattern
- search_file_content: Search for patterns in files
- literature_search: Search literature database (if context requires)
- load_literature: Load literature index
- write_todos: Create task list for complex workflows
- run_shell_command: Execute shell commands

Not all tools are needed. Choose wisely based on data and analysis context."""


def adjust_tools_xml_contract() -> str:
    """是否继续执行工具的 XML 约定。"""
    return (
        "Return only XML: "
        "<adjust><assessment>...</assessment><tools_to_use><tool>...</tool></tools_to_use></adjust>. "
        "If no more tools needed, leave tools_to_use empty."
    )


def visualization_xml_contract() -> str:
    """可视化方案生成的 XML 约定。"""
    return (
        "Return only XML: "
        "<visualizations><viz><use_tool>true</use_tool><tool_name>code_executor</tool_name>"
        "<tool_description>...</tool_description></viz></visualizations>. "
        "If none, leave visualizations empty."
    )
