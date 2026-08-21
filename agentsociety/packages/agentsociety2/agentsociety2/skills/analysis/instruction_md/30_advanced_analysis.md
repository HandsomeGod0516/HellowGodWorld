---
name: advanced_analysis
priority: 30
description: Advanced analytical methodologies for deeper insights beyond basic descriptive statistics.
---

# Advanced Analytical Methodologies

Use these methods when appropriate for deeper analysis. Simple descriptive statistics are also valuable when data is limited.

## Statistical Testing

### Comparing Groups

```python
from scipy import stats

# T-test for two groups
group_a = df[df['group'] == 'A']['value']
group_b = df[df['group'] == 'B']['value']
t_stat, p_value = stats.ttest_ind(group_a, group_b)

# ANOVA for multiple groups
groups = [df[df['group'] == g]['value'] for g in df['group'].unique()]
f_stat, p_value = stats.f_oneway(*groups)

# Chi-square for categorical
contingency = pd.crosstab(df['cat1'], df['cat2'])
chi2, p_value, dof, expected = stats.chi2_contingency(contingency)
```

### Correlation Analysis

```python
# Pearson correlation
corr, p_value = stats.pearsonr(df['x'], df['y'])

# Spearman (rank-based, handles non-linear)
corr, p_value = stats.spearmanr(df['x'], df['y'])
```

### Non-parametric Tests

```python
# Mann-Whitney U (non-parametric t-test)
u_stat, p_value = stats.mannwhitneyu(group_a, group_b)

# Kruskal-Wallis (non-parametric ANOVA)
h_stat, p_value = stats.kruskal(*groups)
```

## Time Series Analysis

```python
# Rolling statistics
df['rolling_mean'] = df['value'].rolling(window=7).mean()
df['rolling_std'] = df['value'].rolling(window=7).std()

# Trend decomposition (if enough data points)
from statsmodels.tsa.seasonal import seasonal_decompose
if len(df) >= 24:  # Need sufficient data
    result = seasonal_decompose(df['value'], period=7)
    result.plot()
```

## Regression Analysis

```python
import statsmodels.api as sm

# Simple linear regression
X = sm.add_constant(df['x'])
model = sm.OLS(df['y'], X).fit()
print(model.summary())

# Multiple regression
X = df[['x1', 'x2', 'x3']]
X = sm.add_constant(X)
model = sm.OLS(df['y'], X).fit()
```

## Network Analysis

For agent interaction data:

```python
import networkx as nx

# Build graph from interactions
G = nx.from_pandas_edgelist(df, 'source', 'target', edge_attr='weight')

# Centrality measures
centrality = nx.degree_centrality(G)
betweenness = nx.betweenness_centrality(G)

# Community detection
communities = nx.community.greedy_modularity_communities(G)

# Visualization
pos = nx.spring_layout(G)
nx.draw(G, pos, with_labels=True, node_size=500)
```

## Clustering

```python
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

# Prepare features
features = df[['feature1', 'feature2', 'feature3']].dropna()
scaler = StandardScaler()
X_scaled = scaler.fit_transform(features)

# K-means clustering
kmeans = KMeans(n_clusters=3, random_state=42)
clusters = kmeans.fit_predict(X_scaled)
```

## Dimensionality Reduction

```python
from sklearn.decomposition import PCA

# PCA for visualization
pca = PCA(n_components=2)
X_pca = pca.fit_transform(X_scaled)

plt.scatter(X_pca[:, 0], X_pca[:, 1], c=clusters)
plt.xlabel(f'PC1 ({pca.explained_variance_ratio_[0]:.1%} variance)')
plt.ylabel(f'PC2 ({pca.explained_variance_ratio_[1]:.1%} variance)')
```

## Inequality Metrics

For distribution analysis:

```python
import numpy as np

def gini_coefficient(values):
    """Calculate Gini coefficient (0 = perfect equality, 1 = perfect inequality)."""
    values = np.array(values)
    values = values[values > 0]  # Remove zeros
    n = len(values)
    if n == 0:
        return 0
    sorted_values = np.sort(values)
    cumsum = np.cumsum(sorted_values)
    return (n + 1 - 2 * np.sum(cumsum) / cumsum[-1]) / n

def lorenz_curve(values):
    """Generate Lorenz curve data points."""
    values = np.sort(values)
    cumsum = np.cumsum(values)
    cumsum = cumsum / cumsum[-1]
    return np.arange(1, len(values) + 1) / len(values), cumsum
```

## Text Analysis

For agent messages or communications:

```python
from collections import Counter
import re

def word_frequency(texts, top_n=20):
    """Count word frequencies from text corpus."""
    words = []
    for text in texts:
        words.extend(re.findall(r'\b\w+\b', text.lower()))
    return Counter(words).most_common(top_n)

def sentiment_basic(texts):
    """Simple sentiment using word lists."""
    positive_words = {'good', 'great', 'happy', 'success', 'excellent'}
    negative_words = {'bad', 'poor', 'sad', 'fail', 'terrible'}

    results = []
    for text in texts:
        words = set(re.findall(r'\b\w+\b', text.lower()))
        pos = len(words & positive_words)
        neg = len(words & negative_words)
        results.append(pos - neg)
    return results
```

## When to Use

| Analysis Type | When Appropriate |
|---------------|------------------|
| Statistical tests | Comparing groups, testing hypotheses |
| Time series | Temporal patterns, trends |
| Regression | Understanding relationships, prediction |
| Network analysis | Agent interactions, social structures |
| Clustering | Finding natural groupings |
| PCA | High-dimensional data visualization |
| Inequality metrics | Distribution analysis, fairness |
| Text analysis | Agent communications, decisions |

## Important Notes

1. **Check data requirements**: Each method has minimum data requirements
2. **Handle missing data**: Most methods require complete cases or imputation
3. **Interpret cautiously**: Statistical significance doesn't imply practical significance
4. **Document assumptions**: Many methods have assumptions (normality, independence, etc.)
5. **Simple is often better**: Basic descriptive analysis is valuable and reliable

Always report results with appropriate context and limitations.
