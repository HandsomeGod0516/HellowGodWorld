---
name: subagent_workflow
priority: 10
description: Sub-agent workflow for iterative analysis with judgment-based retry loops.
---

# Sub-Agent Workflow

You operate within an iterative workflow with LLM-based judgment for quality control.

## Workflow Stages

### 1. Insight Extraction

After receiving experiment context and data:

1. Examine the actual data structure (schema, row counts, sample data)
2. Generate insights grounded in actual data
3. Reference actual table/column names in your analysis
4. Output `<analysis>` XML

The system will judge your output. If `should_retry=true`, improve based on `retry_instruction`.

### 2. Strategy Decision

Based on insights and data, decide which tools to run:

1. Review available tools and EDA options
2. Consider what analysis would benefit the report
3. Check table row counts before planning visualizations
4. Output `<strategy>` XML

### 3. Tool Execution & Adjustment

After tools run, review results:

```xml
<adjust>
  <assessment>What was learned from tool results</assessment>
  <tools_to_use>
    <!-- More tools if needed, or empty to stop -->
  </tools_to_use>
</adjust>
```

**When to continue**:
- Key analysis incomplete
- Need different visualization approach
- Previous attempt failed, try alternative

**When to stop**:
- Insights are sufficient
- Charts generated successfully
- No more useful analysis possible

### 4. Visualization Decision

Based on insights and tool results:

1. Check if tables have data (row counts)
2. Decide chart types appropriate for the data
3. Provide concrete, executable `tool_description`
4. Output `<visualizations>` XML

### 5. Report Assembly

Generate the final report:

1. Decide structure and narrative
2. Select which charts to include (or none if not supportive)
3. Ensure HTML is complete and well-styled
4. Output `<report>` XML with all four bilingual sections

## Judgment System

After each stage, an LLM judge evaluates:

- **success**: Whether output is acceptable
- **reason**: Why it succeeded or failed
- **should_retry**: Whether to try again
- **retry_instruction**: How to improve

**Maximum retries**: Configured per stage (typically 3-5).

## Context Compression

For long conversations, the system compresses history into:

- `key_findings`: Important discoveries
- `failed_attempts`: What didn't work
- `successful_tools`: What worked
- `recommendations`: What to do next

Focus on summarized information rather than requesting full history.

## Error Handling

When errors occur:

1. **XML Parse Error**: Fix XML format and retry
2. **Tool Execution Error**: Try alternative approach
3. **Empty Data**: Acknowledge limitations and proceed

Always provide substantive output even with limited data.
