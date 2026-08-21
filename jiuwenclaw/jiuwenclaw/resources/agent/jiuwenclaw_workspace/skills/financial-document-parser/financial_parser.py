#!/usr/bin/env python3
"""
Financial Document Parser - 財務文件解析工具
支援解析 PDF 發票、收據、銀行對賬單等財務文件
"""

import argparse
import json
import csv
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field, asdict

# PDF 解析依賴
try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    from pdf2image import convert_from_path
    import pytesseract
    HAS_OCR = True
except ImportError:
    HAS_OCR = False


@dataclass
class LineItem:
    """單行專案"""
    description: str
    quantity: float = 1.0
    unit_price: float = 0.0
    total: float = 0.0
    category: str = "Other"


@dataclass
class FinancialDocument:
    """財務文件資料結構"""
    doc_type: str = "Unknown"  # Invoice, Receipt, Statement
    doc_number: str = ""
    date: str = ""
    due_date: str = ""
    vendor_name: str = ""
    vendor_address: str = ""
    client_name: str = ""
    subtotal: float = 0.0
    tax: float = 0.0
    total: float = 0.0
    currency: str = "CNY"
    payment_method: str = ""
    line_items: list = field(default_factory=list)
    raw_text: str = ""
    insights: list = field(default_factory=list)
    flags: list = field(default_factory=list)


class FinancialParser:
    """財務文件解析器"""

    # 費用分類關鍵詞
    CATEGORY_KEYWORDS = {
        "Software": ["軟體", "訂閱", "雲服務", "saas", "adobe", "microsoft", "github", "slack"],
        "Office": ["辦公", "文具", "列印", "影印", "辦公用品"],
        "Travel": ["差旅", "機票", "火車", "酒店", "住宿", "交通", "計程車", "滴滴"],
        "Meals": ["餐飲", "餐費", "午餐", "晚餐", "外賣", "美團", "餓了麼"],
        "Utilities": ["水電", "電費", "水費", "網費", "電話費", "寬頻"],
        "Marketing": ["廣告", "推廣", "營銷", "市場"],
        "Professional": ["諮詢", "法律", "會計", "審計", "顧問"],
        "Equipment": ["裝置", "電腦", "硬體", "伺服器"],
    }

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.doc = FinancialDocument()

    def parse(self) -> FinancialDocument:
        """解析文件"""
        if not self.file_path.exists():
            raise FileNotFoundError(f"檔案不存在: {self.file_path}")

        suffix = self.file_path.suffix.lower()

        if suffix == ".pdf":
            self._parse_pdf()
        elif suffix in [".png", ".jpg", ".jpeg"]:
            self._parse_image()
        elif suffix == ".csv":
            self._parse_csv()
        else:
            raise ValueError(f"不支援的檔案格式: {suffix}")

        # 後處理
        self._detect_doc_type()
        self._categorize_items()
        self._generate_insights()

        return self.doc

    def _parse_pdf(self):
        """解析 PDF 檔案"""
        if not HAS_PDFPLUMBER:
            raise ImportError("需要安裝 pdfplumber: pip install pdfplumber")

        text_content = []
        tables = []

        with pdfplumber.open(self.file_path) as pdf:
            for page in pdf.pages:
                # 提取文字
                text = page.extract_text()
                if text:
                    text_content.append(text)

                # 提取表格
                page_tables = page.extract_tables()
                if page_tables:
                    tables.extend(page_tables)

        self.doc.raw_text = "\n".join(text_content)

        # 如果文字提取失敗，嘗試 OCR
        if not self.doc.raw_text.strip() and HAS_OCR:
            self._ocr_pdf()

        # 解析提取的內容
        self._extract_fields_from_text()
        self._extract_items_from_tables(tables)

    def _ocr_pdf(self):
        """使用 OCR 處理掃描版 PDF"""
        if not HAS_OCR:
            return

        images = convert_from_path(self.file_path)
        text_parts = []

        for img in images:
            text = pytesseract.image_to_string(img, lang='chi_sim+eng')
            text_parts.append(text)

        self.doc.raw_text = "\n".join(text_parts)
        self._extract_fields_from_text()

    def _parse_image(self):
        """解析圖片檔案"""
        if not HAS_OCR:
            raise ImportError("需要安裝 OCR 依賴: pip install pdf2image pytesseract")

        from PIL import Image
        img = Image.open(self.file_path)
        self.doc.raw_text = pytesseract.image_to_string(img, lang='chi_sim+eng')
        self._extract_fields_from_text()

    def _parse_csv(self):
        """解析 CSV 銀行對賬單"""
        with open(self.file_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                item = LineItem(
                    description=row.get('描述', row.get('description', row.get('摘要', ''))),
                    total=self._parse_amount(row.get('金額', row.get('amount', '0')))
                )
                self.doc.line_items.append(item)

        self.doc.doc_type = "Statement"
        self.doc.total = sum(item.total for item in self.doc.line_items)

    def _extract_fields_from_text(self):
        """從文字中提取欄位"""
        import re
        text = self.doc.raw_text
        lines = text.split('\n')

        # 提取發票號 - 支援多種格式
        invoice_patterns = [
            r'Invoice\s+number\s+([A-Z0-9][\w\-\x00]+)',
            r'Invoice\s*(?:no\.?|#)[:\s]*([A-Z0-9][\w\-]+)',
            r'發票號[碼]?[：:]\s*(\S+)',
            r'票號[：:]\s*(\S+)',
        ]
        for pattern in invoice_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                self.doc.doc_number = match.group(1).strip()
                break

        # 提取供應商名稱
        vendor_patterns = [
            r'([A-Z][A-Za-z0-9\s&]+(?:GmbH|LLC|Inc|Ltd|Co\.|Corp|Corporation))',
            r'From[:\s]+([^\n]+)',
            r'供應商[：:]\s*([^\n]+)',
            r'銷售方[：:]\s*([^\n]+)',
        ]
        for pattern in vendor_patterns:
            match = re.search(pattern, text, re.MULTILINE)
            if match:
                vendor = match.group(1).strip()
                # 清理字首
                for prefix in ['Invoice ', 'Receipt ']:
                    if vendor.startswith(prefix):
                        vendor = vendor[len(prefix):]
                self.doc.vendor_name = vendor.strip()
                break

        # 提取日期 - 支援多種格式
        date_patterns = [
            r'(?:Date\s*(?:of\s*issue)?|Issue\s*date)[:\s]+([A-Za-z]+\s+\d{1,2},?\s+\d{4})',
            r'(?:Date\s*(?:of\s*issue)?|Issue\s*date)[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
            r'(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)',
            r'(\d{1,2}[-/]\d{1,2}[-/]\d{4})',
            r'([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})',
        ]
        for pattern in date_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                self.doc.date = match.group(1).strip()
                break

        # 提取貨幣型別
        if 'USD' in text or '$' in text:
            self.doc.currency = 'USD'
        elif 'EUR' in text or '€' in text:
            self.doc.currency = 'EUR'
        elif '¥' in text or '￥' in text or 'CNY' in text or 'RMB' in text:
            self.doc.currency = 'CNY'

        # 提取金額 - 支援多種格式
        amount_patterns = [
            r'(?:Amount\s*due|Total\s*due)[:\s]*[\$€¥￥]?\s*([\d,]+\.?\d*)',
            r'Total[:\s]+[\$€¥￥]?\s*([\d,]+\.?\d*)',
            r'合計[：:]\s*[¥￥]?\s*([\d,]+\.?\d*)',
            r'總[計額][：:]\s*[¥￥]?\s*([\d,]+\.?\d*)',
            r'[\$€]\s*([\d,]+\.?\d*)\s*(?:USD|EUR)?(?:\s+due)?',
        ]
        for pattern in amount_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                self.doc.total = self._parse_amount(match.group(1))
                break

        # 提取小計
        subtotal_patterns = [
            r'Subtotal[:\s]+[\$€¥￥]?\s*([\d,]+\.?\d*)',
            r'小計[：:]\s*[¥￥]?\s*([\d,]+\.?\d*)',
        ]
        for pattern in subtotal_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                self.doc.subtotal = self._parse_amount(match.group(1))
                break

        # 提取稅額
        tax_patterns = [
            r'(?:Tax|VAT)[:\s]+[\$€¥￥]?\s*([\d,]+\.?\d*)',
            r'稅[額款][：:]\s*[¥￥]?\s*([\d,]+\.?\d*)',
        ]
        for pattern in tax_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                self.doc.tax = self._parse_amount(match.group(1))
                break

        # 從文字中提取行專案（如果表格提取失敗）
        self._extract_items_from_text(text)

        # 計算小計（如果未提取到）
        if self.doc.total and self.doc.tax and not self.doc.subtotal:
            self.doc.subtotal = self.doc.total - self.doc.tax
        elif self.doc.total and not self.doc.subtotal:
            self.doc.subtotal = self.doc.total

    def _extract_items_from_text(self, text: str):
        """從文字中提取行專案"""
        import re
        # 匹配類似 "Description Qty Unit price Amount" 後的行
        # 例如: "SEC API (per API Key) 1 $55.00 $55.00"
        item_pattern = r'([A-Za-z][^\n$€¥]+?)\s+(\d+)\s+[\$€¥]?([\d,]+\.?\d*)\s+[\$€¥]?([\d,]+\.?\d*)'

        matches = re.findall(item_pattern, text)
        for match in matches:
            desc, qty, unit_price, total = match
            # 過濾掉表頭行
            if any(kw in desc.lower() for kw in ['description', 'qty', 'quantity', 'unit', 'amount', 'subtotal', 'total']):
                continue
            item = LineItem(
                description=desc.strip(),
                quantity=self._parse_amount(qty),
                unit_price=self._parse_amount(unit_price),
                total=self._parse_amount(total),
            )
            if item.total > 0:
                self.doc.line_items.append(item)

    def _extract_items_from_tables(self, tables: list):
        """從表格中提取行專案"""
        for table in tables:
            if not table or len(table) < 2:
                continue

            # 嘗試識別表頭
            header = table[0]
            if not header:
                continue

            # 查詢關鍵列
            desc_col = None
            qty_col = None
            price_col = None
            total_col = None

            for i, cell in enumerate(header):
                if not cell:
                    continue
                cell_lower = str(cell).lower()
                if any(k in cell_lower for k in ['名稱', '描述', '專案', 'description', 'item']):
                    desc_col = i
                elif any(k in cell_lower for k in ['數量', 'qty', 'quantity']):
                    qty_col = i
                elif any(k in cell_lower for k in ['單價', 'price', 'unit']):
                    price_col = i
                elif any(k in cell_lower for k in ['金額', '合計', 'amount', 'total']):
                    total_col = i

            # 提取資料行
            for row in table[1:]:
                if not row or not any(row):
                    continue

                item = LineItem(
                    description=str(row[desc_col]) if desc_col is not None and desc_col < len(row) else "",
                    quantity=self._parse_amount(row[qty_col]) if qty_col is not None and qty_col < len(row) else 1.0,
                    unit_price=self._parse_amount(row[price_col]) if price_col is not None and price_col < len(row) else 0.0,
                    total=self._parse_amount(row[total_col]) if total_col is not None and total_col < len(row) else 0.0,
                )

                if item.description or item.total:
                    self.doc.line_items.append(item)

    def _parse_amount(self, value) -> float:
        """解析金額字串"""
        if not value:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)

        # 清理字串
        s = str(value).replace(',', '').replace('¥', '').replace('￥', '').replace('$', '').strip()
        try:
            return float(s)
        except ValueError:
            return 0.0

    def _detect_doc_type(self):
        """檢測文件型別"""
        text = self.doc.raw_text.lower()

        if any(k in text for k in ['發票', 'invoice', '增值稅']):
            self.doc.doc_type = "Invoice"
        elif any(k in text for k in ['收據', 'receipt', '小票']):
            self.doc.doc_type = "Receipt"
        elif any(k in text for k in ['對賬單', 'statement', '賬單', '交易明細']):
            self.doc.doc_type = "Statement"
        elif any(k in text for k in ['報銷', 'expense']):
            self.doc.doc_type = "Expense Report"

    def _categorize_items(self):
        """對行專案進行分類"""
        for item in self.doc.line_items:
            desc_lower = item.description.lower()

            for category, keywords in self.CATEGORY_KEYWORDS.items():
                if any(kw in desc_lower for kw in keywords):
                    item.category = category
                    break

    def _generate_insights(self):
        """生成洞察"""
        # 按類別彙總
        category_totals = {}
        for item in self.doc.line_items:
            cat = item.category
            category_totals[cat] = category_totals.get(cat, 0) + item.total

        if category_totals:
            top_category = max(category_totals.keys(), key=lambda k: category_totals[k])
            self.doc.insights.append(f"最大支出類別: {top_category} (¥{category_totals[top_category]:.2f})")

        # 檢測大額交易
        for item in self.doc.line_items:
            if item.total > 10000:
                self.doc.flags.append(f"大額交易: {item.description} (¥{item.total:.2f})")

        # 稅務相關
        if self.doc.tax > 0:
            self.doc.insights.append(f"可抵扣稅額: ¥{self.doc.tax:.2f}")

    def to_dict(self) -> dict:
        """轉換為字典"""
        result = asdict(self.doc)
        result['line_items'] = [asdict(item) for item in self.doc.line_items]
        return result

    def to_json(self) -> str:
        """轉換為 JSON"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_csv(self, output_path: Optional[str] = None) -> str:
        """匯出為 CSV"""
        final_path: str = output_path if output_path else str(self.file_path.with_suffix('.csv'))

        with open(final_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['日期', '供應商', '描述', '類別', '金額', '可抵稅'])

            for item in self.doc.line_items:
                writer.writerow([
                    self.doc.date,
                    self.doc.vendor_name,
                    item.description,
                    item.category,
                    item.total,
                    'Yes' if item.category != 'Other' else 'No'
                ])

        return final_path

    def to_markdown(self) -> str:
        """生成 Markdown 報告"""
        lines = [
            "# 財務文件分析報告",
            "",
            "## 文件資訊",
            f"- **型別**: {self.doc.doc_type}",
            f"- **日期**: {self.doc.date or '未識別'}",
            f"- **單據號**: {self.doc.doc_number or '未識別'}",
            f"- **供應商**: {self.doc.vendor_name or '未識別'}",
            f"- **總金額**: ¥{self.doc.total:,.2f}",
            "",
        ]

        if self.doc.line_items:
            lines.extend([
                "## 明細專案",
                "| 描述 | 數量 | 單價 | 金額 | 類別 |",
                "|------|------|------|------|------|",
            ])
            for item in self.doc.line_items:
                lines.append(
                    f"| {item.description[:30]} | {item.quantity} | ¥{item.unit_price:.2f} | ¥{item.total:.2f} | {item.category} |"
                )
            lines.append("")

        lines.extend([
            "## 財務彙總",
            f"- **小計**: ¥{self.doc.subtotal:,.2f}",
            f"- **稅額**: ¥{self.doc.tax:,.2f}",
            f"- **總計**: ¥{self.doc.total:,.2f}",
            "",
        ])

        # 按類別彙總
        category_totals = {}
        for item in self.doc.line_items:
            cat = item.category
            category_totals[cat] = category_totals.get(cat, 0) + item.total

        if category_totals:
            lines.extend([
                "## 費用分類",
                "| 類別 | 金額 |",
                "|------|------|",
            ])
            for cat, total in sorted(category_totals.items(), key=lambda x: -x[1]):
                lines.append(f"| {cat} | ¥{total:,.2f} |")
            lines.append("")

        if self.doc.insights:
            lines.extend(["## 洞察", ""])
            for insight in self.doc.insights:
                lines.append(f"- ✓ {insight}")
            lines.append("")

        if self.doc.flags:
            lines.extend(["## 需關注項", ""])
            for flag in self.doc.flags:
                lines.append(f"- ⚠ {flag}")
            lines.append("")

        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='財務文件解析工具 - 解析發票、收據、銀行對賬單',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s invoice.pdf                    # 解析 PDF 並輸出 Markdown
  %(prog)s invoice.pdf --format json      # 輸出 JSON 格式
  %(prog)s invoice.pdf --format csv       # 匯出為 CSV
  %(prog)s receipt.jpg                    # 解析圖片收據
  %(prog)s statement.csv                  # 解析 CSV 對賬單
        """
    )

    parser.add_argument('file', help='要解析的檔案路徑 (PDF/圖片/CSV)')
    parser.add_argument('--format', '-f', choices=['markdown', 'json', 'csv', 'all'],
                        default='markdown', help='輸出格式 (預設: markdown)')
    parser.add_argument('--output', '-o', help='輸出檔案路徑 (僅用於 csv 格式)')
    parser.add_argument('--quiet', '-q', action='store_true', help='靜默模式，只輸出結果')

    args = parser.parse_args()

    if not args.quiet:
        print(f"正在解析: {args.file}", file=sys.stderr)

    try:
        parser_obj = FinancialParser(args.file)
        doc = parser_obj.parse()

        if args.format == 'json':
            print(parser_obj.to_json())
        elif args.format == 'csv':
            csv_path = parser_obj.to_csv(args.output)
            if not args.quiet:
                print(f"已匯出到: {csv_path}", file=sys.stderr)
        elif args.format == 'all':
            print(parser_obj.to_markdown())
            print("\n---\n")
            print("## JSON 資料")
            print("```json")
            print(parser_obj.to_json())
            print("```")
        else:
            print(parser_obj.to_markdown())

    except Exception as e:
        print(f"錯誤: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
