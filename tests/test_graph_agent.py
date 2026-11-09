import json
import os
import tempfile
import unittest
from pathlib import Path

from pcmemory.agent import answer_task, build_evidence, focus_queries
from pcmemory.indexer import Indexer
from pcmemory.ontology import version_family_key
from pcmemory.serve import vllm_command
from pcmemory.store import Store


class GraphAgentTests(unittest.TestCase):
    def test_version_family_strips_revision_tokens(self):
        self.assertEqual(version_family_key("方案_v1.txt"), version_family_key("方案_final.txt"))
        self.assertEqual(version_family_key("report_v2.docx"), "report")

    def test_project_version_and_modalities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "Alpha"
            folder.mkdir()
            old = folder / "方案_v1.txt"
            new = folder / "方案_final.txt"
            old.write_text("方案预算 100 万", encoding="utf-8")
            new.write_text("方案预算 120 万，这是最终稿", encoding="utf-8")
            os.utime(old, (1_700_000_000, 1_700_000_000))
            os.utime(new, (1_700_000_100, 1_700_000_100))
            from PIL import Image
            Image.new("RGB", (8, 8), "red").save(folder / "白板.png")
            (folder / "会议.wav").write_bytes(b"RIFF")
            store = Store(root / "index.sqlite")
            try:
                self.assertGreaterEqual(Indexer(store).sync(root)["indexed"], 4)
                file_id = store.search("最终稿")[0]["id"]
                facts = {(item["predicate"], item["value"]) for item in store.facts_for_file(file_id)}
                self.assertIn(("BELONGS_TO_PROJECT", "alpha"), facts)
                self.assertIn(("HAS_MODALITY", "text"), facts)
                self.assertEqual(store.version_group(file_id)[0]["name"], "方案_final.txt")
                relations = {row["relation_type"] for row in store.related_files([file_id], 10)}
                self.assertIn("NEWER_VERSION", relations)
                modalities = {row[0] for row in store.modality_counts()}
                self.assertTrue({"text", "image", "audio"} <= modalities)
                shown = "\n".join(chunk["content"] for chunk in store.get_chunks(store.search("白板")[0]["id"]))
                self.assertIn("[image]", shown)
                heard = "\n".join(chunk["content"] for chunk in store.get_chunks(store.search("会议.wav")[0]["id"]))
                self.assertIn("[audio]", heard)

                def complete(messages):
                    self.assertIn("多版本材料", messages[1]["content"])
                    self.assertIn("BELONGS_TO_PROJECT", messages[1]["content"])
                    return "最新版本是方案_final.txt，属于项目 alpha。"

                self.assertIn("方案_final", answer_task(store, "哪个是最新版本", complete=complete))
            finally:
                store.close()

    def test_catalog_summary_and_same_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Alpha").mkdir()
            (root / "Beta").mkdir()
            (root / "Alpha" / "note.txt").write_text("同一份实验记录", encoding="utf-8")
            (root / "Beta" / "note.txt").write_text("同一份实验记录", encoding="utf-8")
            (root / "metadata.json").write_text(json.dumps([{
                "relative_path": "Alpha/note.txt",
                "summary": "阿尔法项目的实验记录",
                "keywords": ["experiment"],
                "modality": "txt",
            }], ensure_ascii=False), encoding="utf-8")
            store = Store(root / "index.sqlite")
            try:
                Indexer(store).sync(root)
                hits = store.search("阿尔法项目的实验记录")
                self.assertTrue(hits)
                self.assertIn("Alpha", hits[0]["path"])
                file_id = hits[0]["id"]
                relations = {row["relation_type"] for row in store.related_files([file_id], 10)}
                self.assertIn("SAME_CONTENT", relations)
            finally:
                store.close()

    def test_question_uses_filename_and_short_phrase(self):
        self.assertIn("example.ogg", focus_queries("example.ogg 这段音频转写了什么内容，文件在哪个项目目录？"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "08_Media"
            media.mkdir()
            (media / "flower.md").write_text("这是花朵视频的说明", encoding="utf-8")
            store = Store(root / "index.sqlite")
            try:
                Indexer(store).sync(root)
                evidence = build_evidence(store, "花朵视频在哪个文件夹，它是什么模态？")
                self.assertIn("flower.md", evidence)
            finally:
                store.close()

    def test_same_bytes_are_ordered_newest_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            early = root / "Alpha"
            late = root / "Beta"
            early.mkdir()
            late.mkdir()
            old = early / "损失曲线.png.txt"
            new = late / "损失曲线.png.txt"
            old.write_text("GPT-2 损失曲线", encoding="utf-8")
            new.write_text("GPT-2 损失曲线", encoding="utf-8")
            os.utime(old, (1_700_000_000, 1_700_000_000))
            os.utime(new, (1_700_000_500, 1_700_000_500))
            store = Store(root / "index.sqlite")
            try:
                Indexer(store).sync(root)
                evidence = build_evidence(store, "GPT-2 损失曲线最新的一份在哪个项目")
                section = evidence.split("[同一内容跨目录，新到旧", 1)[1]
                self.assertLess(section.index("项目=Beta"), section.index("项目=Alpha"))
                self.assertIn("最新一份", section)
            finally:
                store.close()

    def test_vllm_command_targets_qwen3_8b(self):
        command = vllm_command()
        self.assertIn("Qwen/Qwen3-8B", command)
        self.assertIn("vllm.entrypoints.openai.api_server", command)


if __name__ == "__main__":
    unittest.main()
