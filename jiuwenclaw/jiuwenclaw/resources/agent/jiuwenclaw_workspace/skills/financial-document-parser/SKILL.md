---
name: financial-document-parser
description: Extract and analyze data from invoices, receipts, bank statements, and financial documents. Categorize expenses, track recurring charges, and generate expense reports. Use when user provides financial PDFs or images.
---

# Financial Document Parser

解析財務文件（發票、收據、銀行對賬單）並提取結構化資料。

## 核心指令碼

本 skill 包含一個可複用的 Python 指令碼：`financial_parser.py`

### 依賴安裝

```bash
pip install pdfplumber

# 可選：OCR 支援（用於掃描版 PDF 和圖片）
pip install pdf2image pytesseract
# 還需要安裝 tesseract-ocr 系統包
# Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-chi-sim
```

### 命令列用法

```bash
# 解析 PDF 發票，輸出 Markdown 報告
python financial_parser.py invoice.pdf

# 輸出 JSON 格式
python financial_parser.py invoice.pdf --format json

# 匯出為 CSV
python financial_parser.py invoice.pdf --format csv

# 解析圖片收據
python financial_parser.py receipt.jpg

# 解析 CSV 銀行對賬單
python financial_parser.py statement.csv

# 完整輸出（Markdown + JSON）
python financial_parser.py invoice.pdf --format all
```

### Python API 用法

```python
from financial_parser import FinancialParser

# 解析文件
parser = FinancialParser("/path/to/invoice.pdf")
doc = parser.parse()

# 獲取結構化資料
print(doc.doc_type)      # Invoice, Receipt, Statement
print(doc.total)         # 總金額
print(doc.line_items)    # 明細專案列表

# 匯出
print(parser.to_markdown())  # Markdown 報告
print(parser.to_json())      # JSON 資料
parser.to_csv("output.csv")  # CSV 檔案
```

## When to Use This Skill

當使用者：
- 提供發票、收據或銀行對賬單檔案
- 要求 "解析這張發票" 或 "提取收據資料"
- 需要費用分類
- 想要追蹤消費模式
- 要求生成費用報告
- 提供 PDF 或圖片格式的財務文件

## 執行流程

1. **確認檔案路徑** - 獲取使用者提供的檔案路徑
2. **執行解析指令碼** - 使用 Bash 工具執行：
   ```bash
   python financial_parser.py <檔案路徑> --format all
   ```
3. **展示結果** - 將解析結果展示給使用者
4. **按需匯出** - 如使用者需要，匯出 CSV 或 JSON

## 支援的文件型別

| 型別 | 格式 | 提取內容 |
|------|------|----------|
| 發票 | PDF | 發票號、日期、供應商、明細、稅額、總額 |
| 收據 | PDF/圖片 | 商戶、日期、商品、金額 |
| 銀行對賬單 | PDF/CSV | 交易明細、餘額、費用 |
| 信用卡賬單 | PDF | 交易記錄、還款資訊 |

## 費用分類

指令碼自動將費用分類為：
- **Software**: 軟體、訂閱、雲服務
- **Office**: 辦公用品、列印
- **Travel**: 差旅、機票、酒店
- **Meals**: 餐飲、外賣
- **Utilities**: 水電、網費
- **Marketing**: 廣告、推廣
- **Professional**: 諮詢、法律、會計
- **Equipment**: 裝置、硬體
- **Other**: 其他

## 輸出示例

```markdown
# 財務文件分析報告

## 文件資訊
- **型別**: Invoice
- **日期**: 2025-01-15
- **單據號**: INV-2025-0042
- **供應商**: 某某科技有限公司
- **總金額**: ¥12,580.00

## 明細專案
| 描述 | 數量 | 單價 | 金額 | 類別 |
|------|------|------|------|------|
| 雲伺服器年費 | 1 | ¥9,800.00 | ¥9,800.00 | Software |
| 技術支援服務 | 1 | ¥2,000.00 | ¥2,000.00 | Professional |

## 財務彙總
- **小計**: ¥11,150.94
- **稅額**: ¥1,429.06
- **總計**: ¥12,580.00

## 費用分類
| 類別 | 金額 |
|------|------|
| Software | ¥9,800.00 |
| Professional | ¥2,000.00 |

## 洞察
- ✓ 最大支出類別: Software (¥9,800.00)
- ✓ 可抵扣稅額: ¥1,429.06

## 需關注項
- ⚠ 大額交易: 雲伺服器年費 (¥9,800.00)
```

## 批次處理

處理多個檔案：

```bash
# 批次解析目錄下所有 PDF
for f in /path/to/invoices/*.pdf; do
  python financial_parser.py "$f" --format csv
done
```

## 注意事項

- 保持金額精確，不要四捨五入
- 敏感資訊（賬號）會自動脫敏
- 如果文字提取失敗，會自動嘗試 OCR
- 支援中英文混合文件
