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
            svg = root / "icon.svg"
            svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><title>GitHub Mark</title></svg>', encoding="utf-8")
            self.assertIn("GitHub Mark", extract_text(svg) or "")
            archive = root / "snap.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("readme.md", "hello")
            self.assertIn("readme.md", extract_text(archive) or "")
            secret = root / "ca.key"
            secret.write_text("-----BEGIN PRIVATE KEY-----\nSECRET-MATERIAL\n", encoding="utf-8")
            hidden = extract_text(secret) or ""
            self.assertIn("[secret]", hidden)
            self.assertNotIn("SECRET-MATERIAL", hidden)


if __name__ == "__main__":
    unittest.main()