---
name: xml_contract
priority: 0
description: Strict XML output contract for analysis, judgment, and report generation.
required: true
---

# XML Output Contract (Strict)

- Return only XML requested by the prompt; do not output JSON.
- Do not include prose outside XML tags.
- For `<report>` XML, always include ALL FOUR bilingual sections:
  - `<markdown_zh><![CDATA[中文 Markdown 报告]]></markdown_zh>`
  - `<html_zh><![CDATA[中文 HTML 完整文档]]></html_zh>`
  - `<markdown_en><![CDATA[English Markdown report]]></markdown_en>`
  - `<html_en><![CDATA[English HTML complete document]]></html_en>`
- Each section must have substantive content (not empty placeholders).
- Use `<![CDATA[...]]>` to wrap content that may contain special characters.
- Keep tags stable and parsable; never change tag names.
- For judgment XML: `<judgment><success>true/false</success><reason>...</reason>...</judgment>`
