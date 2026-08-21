"""
分析模組與 LLM 互動的 **輸出契約**（XML 片段與說明函式）。

可組合的 **自然語言能力說明** 見 `instruction_md/`（`utils.get_analysis_skills`）；模組總覽見 `README.md`。
"""

# 通用裁判 XML 格式（分析/策略/視覺化/報告等判斷）
JUDGMENT_XML = (
    "<judgment><success>true</success><reason>...</reason>"
    "<should_retry>false</should_retry><retry_instruction>...</retry_instruction></judgment>"
)

# 報告生成 XML 格式（中英雙語各一份 Markdown + HTML，圖表路徑與 assets 引用保持一致）
REPORT_XML = (
    "<report>"
    "<markdown_zh><![CDATA[Chinese Markdown]]></markdown_zh>"
    "<html_zh><![CDATA[Chinese full HTML document]]></html_zh>"
    "<markdown_en><![CDATA[English Markdown]]></markdown_en>"
    "<html_en><![CDATA[English full HTML document]]></html_en>"
    "</report>"
)

# 報告裁判 XML
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
    """返回裁判類 prompt 的 XML 要求部分。"""
    return f"Return only XML: {JUDGMENT_XML}{suffix}"


def report_xml_instruction() -> str:
    """返回報告生成的 XML 要求。"""
    return (
        f"**Must** return only XML: {REPORT_XML} "
        "Chinese sections use professional 簡體中文; English sections are full English. "
        "Both locales must embed the same charts using the same relative paths "
        '(e.g. `assets/file.png`).'
    )


def report_judgment_prompt() -> str:
    """返回報告裁判的 XML 要求。"""
    return f"Return only XML: {REPORT_JUDGMENT_XML}"


def summary_xml_contract() -> str:
    """返回上下文摘要的 XML 要求。"""
    return f"Return only XML: {SUMMARY_XML}"


def analysis_xml_contract() -> str:
    """分析結果生成的 XML 約定。"""
    return """Return only XML:
<analysis>
  <insights><item>...</item><item>...</item></insights>
  <findings><item>...</item></findings>
  <conclusions>...</conclusions>
  <recommendations><item>...</item></recommendations>
</analysis>"""


def strategy_xml_contract() -> str:
    """分析策略生成的 XML 約定。"""
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
    """是否繼續執行工具的 XML 約定。"""
    return (
        "Return only XML: "
        "<adjust><assessment>...</assessment><tools_to_use><tool>...</tool></tools_to_use></adjust>. "
        "If no more tools needed, leave tools_to_use empty."
    )


def visualization_xml_contract() -> str:
    """視覺化方案生成的 XML 約定。"""
    return (
        "Return only XML: "
        "<visualizations><viz><use_tool>true</use_tool><tool_name>code_executor</tool_name>"
        "<tool_description>...</tool_description></viz></visualizations>. "
        "If none, leave visualizations empty."
    )
