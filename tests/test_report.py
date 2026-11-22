"""Portable report tests: safety, evidence status, references, and overlays."""

from __future__ import annotations

import base64
import copy
import tempfile
import unittest
from pathlib import Path

from gap_manifest.report import write_html_report


def fixture() -> tuple[dict, dict]:
    ref = {"source_id": "pdf1", "page": 1, "bbox": [10, 20, 60, 80],
           "raw_text": "<script>danger()</script>", "method": "visual", "fields": ["original_label"]}
    manifest = {
        "schema_version": "1.0", "case_id": "test-<candidate>",
        "sources": [{"id": "pdf1", "path": "data/input.pdf", "sha256": "a" * 64,
                     "pages": [{"number": 1, "width_pt": 100, "height_pt": 150}]}],
        "selection": {"id": "candidate", "review_status": "pending", "reviewed_by": None,
                      "reviewed_at": None, "notes": "Требуется подтверждение преподавателя",
                      "regions": [{"source_id": "pdf1", "page": 1, "bbox": [5, 5, 90, 140]}],
                      "required_minima": {"nodes": 10, "pipes": 12, "sources": 2, "valves": 4}},
        "nodes": [{"id": "n1", "original_label": "Куст <1>", "review_status": "pending", "kind": "source",
                   "context": "тест", "position": [20, 30], "source_refs": [ref]}],
        "pipes": [{"id": "p1", "original_label": "Труба", "review_status": "pending", "from_node": "n1",
                   "to_node": "n2", "parameters": {"length_m": 25, "outer_diameter_mm": None},
                   "role": "pipeline", "line_class": "oil", "active_state": "unknown", "source_refs": []}],
        "valves": [{"id": "v1", "original_label": "Клапан", "review_status": "pending", "pipe_id": "p1",
                    "physical_state": "closed", "size_dn_mm": 150, "pressure_rating_raw": "150×40", "source_refs": [ref]}],
        "ambiguities": [{"id": "a1", "kind": "crossing", "description": "Соединение?", "status": "open",
                         "affected_ids": ["n1"], "source_refs": [ref], "resolution": None}],
    }
    validation = {"execution_status": "SUCCESS", "ready_for_transfer": False, "stats": {"nodes": 1},
                  "topology": {"physical": {"component_count": 1}, "guaranteed_flow": {"components": [["n1"], ["n2"]]},
                               "possible_flow": {"component_count": 1}},
                  "issues": [{"code": "DRAFT", "severity": "warning", "object_id": "n1",
                              "message": "Нужна проверка", "source_refs": [ref]}]}
    return manifest, validation


class HtmlReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manifest, self.validation = fixture()

    def tearDown(self):
        self.temp.cleanup()

    def render(self, **kwargs):
        path = self.root / "nested" / "report.html"
        result = write_html_report(self.manifest, self.validation, path, **kwargs)
        self.assertEqual(result, path)
        return path.read_text(encoding="utf-8")

    def test_draft_missing_export_and_escape(self):
        result = self.render()
        self.assertIn("Черновик: к переносу не готов", result)
        self.assertIn("Реальный экспорт GAP не предоставлен", result)
        self.assertIn("NOT_ASSESSED", result)
        self.assertIn("&lt;script&gt;danger()&lt;/script&gt;", result)
        self.assertNotIn("<script>", result)
        self.assertIn("Куст &lt;1&gt;", result)
        self.assertIn("a" * 64, result)
        self.assertIn("Гарантированные пути протока", result)
        self.assertIn("Длина, м", result)
        self.assertIn("150×40", result)

    def test_synthetic_even_with_computed_ok_is_not_acceptance(self):
        result = self.render(comparison={
            "execution_status": "SUCCESS", "assessment_status": "OK", "structural_status": "OK",
            "numeric_status": "NOT_ASSESSED", "model": {"evidence": "synthetic"}, "issues": [], "summary": {},
        })
        self.assertIn("Реальный экспорт GAP не подтверждён", result)
        self.assertIn("пригоден только для проверки программы", result)
        self.assertIn("не является приёмкой", result)
        self.assertNotIn("Валидация заявляет готовность", result)

    def test_overlay_is_self_contained_and_filters_page(self):
        image = self.root / "page.png"
        image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2bcAAAAAASUVORK5CYII="))
        self.manifest["ambiguities"].append({
            "id": "other-page", "status": "open", "source_refs": [{"source_id": "pdf1", "page": 2, "bbox": [1, 2, 3, 4]}]
        })
        result = self.render(page_image=image)
        self.assertIn('viewBox="0 0 100 150"', result)
        self.assertIn("data:image/png;base64,", result)
        self.assertIn('class="ambiguity"', result)
        self.assertIn('class="issue"', result)
        self.assertIn('x="10" y="20" width="50" height="60"', result)
        self.assertNotIn("<title>other-page</title>", result)
        self.assertNotIn(str(image), result)
        self.assertNotIn("https://", result)

    def test_text_source_without_bbox_and_missing_page_image(self):
        self.manifest["nodes"][0]["source_refs"].append({
            "source_id": "legend", "text_file": "README.txt", "line_start": 5, "line_end": 9,
            "raw_text": 'Условные обозначения: "тест"', "method": "text_layer", "fields": ["kind"],
        })
        result = self.render(page_image=self.root / "missing.png")
        self.assertIn("README.txt", result)
        self.assertIn("строки 5–9", result)
        self.assertIn("Не удалось прочитать изображение", result)

    def test_flat_topology_counters_and_duplicate_overlay_areas(self):
        self.validation["topology"] = {
            "physical_components": [["n1", "n2"]],
            "guaranteed_flow_components": [["n1"], ["n2"]],
            "possible_flow_components": [["n1", "n2"]],
        }
        issue = copy.deepcopy(self.validation["issues"][0])
        self.validation["issues"].extend([issue] * 100)
        image = self.root / "page.png"
        image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2bcAAAAAASUVORK5CYII="))
        result = self.render(page_image=image)
        self.assertIn("Физическая связность</td><td>1</td>", result)
        self.assertIn("Гарантированные пути протока</td><td>2</td>", result)
        self.assertIn("Возможные пути протока</td><td>1</td>", result)
        self.assertEqual(result.count('<g class="issue">'), 1)
        self.assertIn("Показано рамок: 3", result)

    def test_does_not_modify_input_and_real_export_preserves_statuses(self):
        original = copy.deepcopy((self.manifest, self.validation))
        result = self.render(comparison={
            "model": {"evidence": "real", "path": "model.gap"}, "assessment_status": "OUT_OF_TOLERANCE",
            "structural_status": "OK", "numeric_status": "OUT_OF_TOLERANCE", "execution_status": "SUCCESS",
            "issues": [{"severity": "error", "code": "NUMERIC", "message": "Неверная длина <25>", "source_refs": []}],
        })
        self.assertEqual((self.manifest, self.validation), original)
        self.assertIn("OUT_OF_TOLERANCE", result)
        self.assertIn("Неверная длина &lt;25&gt;", result)
        self.assertIn("исходный кандидат ещё не готов", result)

    def test_invalid_nested_types_still_allow_showing_validation_errors(self):
        self.manifest["selection"]["required_minima"] = 123
        self.manifest["pipes"][0]["parameters"] = ["invalid"]
        self.manifest["sources"][0]["pages"][0]["width_pt"] = 10 ** 1000
        result = self.render(page_image=self.root / "page.png", comparison={"model": ["invalid"]})
        self.assertIn("Черновик: к переносу не готов", result)
        self.assertIn("отсутствуют корректные размеры страницы", result)
        self.manifest["selection"] = "invalid"
        self.assertIn("Черновик: к переносу не готов", self.render())


if __name__ == "__main__":
    unittest.main()
