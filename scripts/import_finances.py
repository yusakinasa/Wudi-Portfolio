"""Import the personal daily-expense XLSX layout without third-party packages.

Each table has contiguous 日期 / 餐饮 / 交通 / 其他 / 总计 / 备注 headers.
Supports parallel month tables, additional sheets, shared/inline strings,
both Excel date epochs, and ISO date text. Amounts are exported as integer fen.
Daily totals are recomputed, never summed from cached monthly summary formulas.
"""

import json
import posixpath
import re
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/content/finances/finance.xlsx"
DESTINATION = ROOT / "src/generated/finances.json"
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
HEADERS = ["日期", "餐饮", "交通", "其他", "总计", "备注"]
KEYS = ["food", "transport", "other"]


def column(reference):
    result = 0
    for letter in re.match(r"[A-Z]+", reference).group():
        result = result * 26 + ord(letter) - 64
    return result


def cell_text(cell, shared):
    if cell is None:
        return ""
    kind = cell.get("t")
    raw = cell.findtext("s:v", default="", namespaces=NS)
    if kind == "s":
        return shared[int(raw)]
    if kind == "inlineStr":
        return "".join(cell.itertext()).strip()
    if kind == "e":
        raise ValueError(f"单元格 {cell.get('r')} 有 Excel 错误：{raw}")
    return raw.strip()


def amount(cell, shared):
    raw = cell_text(cell, shared)
    if not raw:
        if cell is not None and cell.find("s:f", NS) is not None:
            raise ValueError(f"{cell.get('r')} 的金额公式没有缓存，请用 Excel 重新计算并保存")
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError(f"无效金额 {cell.get('r')}: {raw}") from error
    if not value.is_finite() or value < 0 or value * 100 != (value * 100).to_integral_value():
        raise ValueError(f"{cell.get('r')} 应为最多两位小数的非负支出金额")
    return int(value * 100)


def read_date(raw, epoch_1904):
    if not raw:
        return None
    if re.fullmatch(r"\d+(?:\.0+)?", raw):
        serial = int(Decimal(raw))
        if not 1 <= serial <= 100000:
            raise ValueError(f"无效 Excel 日期：{raw}")
        epoch = date(1904, 1, 1) if epoch_1904 else date(1899, 12, 30)
        return epoch + timedelta(days=serial)
    if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", raw):
        parts = re.split(r"[-/]", raw.split(" ")[0].split("T")[0])
        return date(*map(int, parts))
    return None  # 月度汇总、占比、平均等非日期标签。


def import_workbook(source=SOURCE):
    records = {}
    warnings = []
    table_count = 0
    with ZipFile(source) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(item.itertext()) for item in strings]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        properties = workbook.find("s:workbookPr", NS)
        epoch_1904 = properties is not None and properties.get("date1904") in ("1", "true")
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.get("Id"): item.get("Target") for item in relations}
        for sheet in workbook.findall("s:sheets/s:sheet", NS):
            target = targets[sheet.get(f"{{{REL}}}id")]
            sheet_path = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            xml = ET.fromstring(archive.read(sheet_path))
            merged_starts = {item.get("ref").split(":")[0] for item in xml.findall("s:mergeCells/s:mergeCell", NS)}
            rows = [({column(c.get("r")): c for c in row}, int(row.get("r"))) for row in xml.findall("s:sheetData/s:row", NS)]
            tables = []
            for cells, number in rows:
                for start, cell in cells.items():
                    if cell_text(cell, shared) == HEADERS[0] and [cell_text(cells.get(start + i), shared) for i in range(6)] == HEADERS:
                        tables.append((start, number))
            table_count += len(tables)
            for start, header_row in tables:
                next_header = min((number for other_start, number in tables if other_start == start and number > header_row), default=float("inf"))
                for cells, row_number in rows:
                    if row_number <= header_row or row_number >= next_header:
                        continue
                    date_cell = cells.get(start)
                    if date_cell is not None and date_cell.get("r") in merged_starts:
                        continue  # 以日期格式显示的合并月份标题不是账目。
                    raw_date = cell_text(date_cell, shared)
                    day = read_date(raw_date, epoch_1904)
                    if day is None:
                        if raw_date and raw_date not in HEADERS + ["占比", "平均", "合计", "小计"] and any(cell_text(cells.get(start + i), shared) for i in range(1, 4)):
                            raise ValueError(f"{sheet.get('name')} 第 {row_number} 行日期无效：{raw_date}")
                        continue
                    categories = {key: amount(cells.get(start + i + 1), shared) for i, key in enumerate(KEYS)}
                    total_cell = cells.get(start + 4)
                    reported = None if total_cell is not None and total_cell.find("s:f", NS) is not None and not cell_text(total_cell, shared) else amount(total_cell, shared)
                    if all(value is None for value in categories.values()):
                        if reported not in (None, 0):
                            raise ValueError(f"{day}: 已有总计但没有分类金额，请填写分类")
                        if reported != 0:
                            continue  # 只填日期的未来行尚未记账。
                    total = sum(value or 0 for value in categories.values())
                    if reported is not None and reported != total:
                        if total_cell.find("s:f", NS) is None:
                            raise ValueError(f"{day}: 手填总计与分类金额不一致")
                        warnings.append(f"{day} 的总计公式缓存过期，已按分类重算")
                    key = day.isoformat()
                    if key in records:
                        raise ValueError(f"重复日期 {key}，请合并同一天的支出")
                    records[key] = {"date": key, **categories, "total": total, "note": cell_text(cells.get(start + 5), shared)}
    if not table_count or not records:
        raise ValueError("未找到支出记录，表头必须依次为：" + "、".join(HEADERS))
    return {"currency": "CNY", "source": source.name, "warnings": warnings, "records": [records[key] for key in sorted(records)]}


def main():
    result = import_workbook()
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Finances: 已同步 {len(result['records'])} 天；累计 ¥{sum(row['total'] for row in result['records']) / 100:,.2f}")
    for warning in result["warnings"]:
        print(warning, file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        sys.exit(f"财务表格导入失败：{error}")
