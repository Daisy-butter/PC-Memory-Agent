import tempfile
import unittest
import zipfile
from pathlib import Path

from pcmemory.extract import extract_text


class StructuredExtractionTests(unittest.TestCase):
    def test_xlsx_and_pptx_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xlsx = root / "graph.xlsx"
            with zipfile.ZipFile(xlsx, "w") as archive:
                archive.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>GraphData</t></si></sst>')
                archive.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c t="s"><v>0</v></c></row></sheetData></worksheet>')
            self.assertEqual(extract_text(xlsx), "GraphData")
            pptx = root / "graph.pptx"
            with zipfile.ZipFile(pptx, "w") as archive:
                archive.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>KnowledgeGraph</a:t></p:sld>')
            self.assertEqual(extract_text(pptx), "KnowledgeGraph")


if __name__ == "__main__":
    unittest.main()