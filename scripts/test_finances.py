"""Regression checks for expense amounts, missing values and XLSX table layout."""
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
from import_finances import import_workbook


def workbook(rows, merged="", epoch=""):
    header = '<row r="1">' + ''.join(f'<c r="{letter}1" t="inlineStr"><is><t>{name}</t></is></c>' for letter, name in zip("ABCDEF", ["日期", "餐饮", "交通", "其他", "总计", "备注"])) + '</row>'
    return f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{header}{rows}</sheetData>{merged}</worksheet>'


class ImportTests(unittest.TestCase):
    def parse(self, rows, merged="", epoch=""):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "fixture.xlsx"
            with ZipFile(source, "w") as archive:
                archive.writestr("xl/workbook.xml", f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><workbookPr {epoch}/><sheets><sheet name="Ledger" r:id="rId1"/></sheets></workbook>')
                archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
                archive.writestr("xl/worksheets/sheet1.xml", workbook(rows, merged))
            return import_workbook(source)

    def test_real_workbook_totals(self):
        records = import_workbook()["records"]
        self.assertEqual(len(records), 40)
        self.assertEqual(sum(row["total"] for row in records), 271600)
        self.assertEqual(sum(row["total"] for row in records if row["date"].startswith("2026-08")), 75800)
        self.assertEqual(sum(row["total"] for row in records if row["date"].startswith("2026-09")), 195800)
        self.assertEqual(sum(row["food"] or 0 for row in records), 177700)
        self.assertEqual(sum(row["transport"] or 0 for row in records), 44500)
        self.assertEqual(sum(row["other"] or 0 for row in records), 49400)
        self.assertEqual(next(row["total"] for row in records if row["date"] == "2026-09-14"), 0)

    def test_missing_zero_month_heading_and_summary(self):
        rows = '<row r="2"><c r="A2"><v>46235</v></c></row><row r="3"><c r="A3"><v>46256</v></c><c r="B3"><v>0</v></c><c r="D3"><v>12.34</v></c></row><row r="4"><c r="A4" t="inlineStr"><is><t>总计</t></is></c><c r="B4"><v>999</v></c></row>'
        records = self.parse(rows, '<mergeCells><mergeCell ref="A2:F2"/></mergeCells>')["records"]
        self.assertEqual(records, [{"date": "2026-08-22", "food": 0, "transport": None, "other": 1234, "total": 1234, "note": ""}])

    def test_duplicate_date_rejected(self):
        rows = ''.join(f'<row r="{i}"><c r="A{i}"><v>46256</v></c><c r="B{i}"><v>1</v></c></row>' for i in [2, 3])
        with self.assertRaisesRegex(ValueError, "重复日期"):
            self.parse(rows)

    def test_wrong_manual_total_rejected(self):
        with self.assertRaisesRegex(ValueError, "手填总计"):
            self.parse('<row r="2"><c r="A2"><v>46256</v></c><c r="B2"><v>1</v></c><c r="E2"><v>2</v></c></row>')

    def test_stale_formula_total_recomputed(self):
        result = self.parse('<row r="2"><c r="A2"><v>46256</v></c><c r="B2"><v>1</v></c><c r="E2"><f>SUM(B2:D2)</f><v>2</v></c></row>')
        self.assertEqual(result["records"][0]["total"], 100)
        self.assertEqual(len(result["warnings"]), 1)

    def test_bad_amount_and_missing_formula_cache_rejected(self):
        for value in ['<v>-1</v>', '<v>1.001</v>', '<f>SUM(B3:B4)</f>']:
            with self.assertRaises(ValueError):
                self.parse(f'<row r="2"><c r="A2"><v>46256</v></c><c r="B2">{value}</c></row>')

    def test_1904_epoch_and_empty_future_day(self):
        records = self.parse('<row r="2"><c r="A2"><v>1</v></c><c r="B2"><v>0</v></c></row><row r="3"><c r="A3"><v>2</v></c></row>', epoch='date1904="1"')["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["date"], "1904-01-02")

    def test_total_formula_without_cache_recomputed(self):
        rows = '<row r="2"><c r="A2"><v>46256</v></c><c r="B2"><v>2.35</v></c><c r="E2"><f>SUM(B2:D2)</f></c></row>'
        self.assertEqual(self.parse(rows)["records"][0]["total"], 235)


if __name__ == "__main__":
    unittest.main()
