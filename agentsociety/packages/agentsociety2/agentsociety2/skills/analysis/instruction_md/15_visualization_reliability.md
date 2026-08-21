---
name: visualization_reliability
priority: 15
description: Best practices for reliable visualization generation with code execution.
---

# Visualization Reliability

## Pre-Flight Checks

Before generating any visualization:

1. **Check row counts**: `SELECT COUNT(*) FROM table_name`
2. **Verify columns exist**: Query schema first
3. **Handle empty tables**: Generate diagnostic charts or acknowledge limitations
4. **Sample large data**: Use `df.sample(n=10000)` for >50k rows

## Chart Type Selection

| Data Type | Recommended Charts | Avoid |
|-----------|-------------------|-------|
| Numerical distribution | Histogram, KDE, Box, Violin | Scatter (too many points) |
| Categorical comparison | Bar, Box, Violin | Pie (>5 categories) |
| Correlation | Heatmap, Scatter (sampled) | 3D plots |
| Time series | Line with confidence bands | Overly dense scatter |
| Network/Graph | networkx visualization | Complex interactive plots |
| Geographic | Map visualization (if coords) | Text-based representation |

## Code Generation Guidelines

### Always Include

```python
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
```

### Safe Database Query

```python
import sqlite3
conn = sqlite3.connect('sqlite.db')
cursor = conn.cursor()

# Verify table exists
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = {row[0] for row in cursor.fetchall()}

if 'expected_table' not in tables:
    print("Table not found. Available:", tables)
    import sys
    sys.exit(0)

# Check row count before plotting
cursor.execute("SELECT COUNT(*) FROM expected_table")
count = cursor.fetchone()[0]
if count == 0:
    print("No data to visualize")
    import sys
    sys.exit(0)
```

### Memory-Safe Plotting

```python
# For large datasets
if len(df) > 50000:
    df_sample = df.sample(n=min(10000, len(df)), random_state=42)
else:
    df_sample = df

# Use sampled data for scatter/complex plots
plt.scatter(df_sample['x'], df_sample['y'])
```

### Save Properly

```python
plt.savefig('chart_name.png', dpi=150, bbox_inches='tight')
plt.close()  # Free memory
```

## Error Recovery Patterns

### Table Not Found

```python
if table_name not in actual_tables:
    print(f"Available tables: {actual_tables}")
    # Either use alternative table or exit gracefully
```

### Empty Result

```python
if df.empty:
    print("No data matching criteria")
    # Generate diagnostic chart showing why
    # Or proceed with alternative analysis
```

### Memory Issues

```python
# Use SQL aggregation instead of loading all data
query = """
    SELECT category, COUNT(*), AVG(value)
    FROM large_table
    GROUP BY category
"""
df = pd.read_sql_query(query, conn)
```

## Visualization Quality Checklist

Before finalizing:

- [ ] Proper axis labels and title
- [ ] Legend if multiple series
- [ ] Appropriate figure size
- [ ] Readable font sizes (>=10pt)
- [ ] No overlapping text
- [ ] Color-blind friendly palette (optional but recommended)

## Common Pitfalls

| Issue | Solution |
|-------|----------|
| "Table not found" | Query `sqlite_master` first |
| Empty chart | Check row count before plotting |
| Memory error | Sample data or use SQL aggregation |
| Overlapping labels | Rotate or reduce number of labels |
| Slow rendering | Reduce data points or use simpler chart |

## Output Requirements

Generated charts are saved as PNG files in the working directory. They will be:
1. Collected automatically by the pipeline
2. Copied to `assets/` directory
3. Embedded in the final HTML report

Use `plt.savefig('descriptive_name.png', dpi=150, bbox_inches='tight')` for best results.
