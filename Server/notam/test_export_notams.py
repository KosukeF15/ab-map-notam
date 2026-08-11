import unittest
from xml.etree import ElementTree

from export_notams import geometry


class GeometryTests(unittest.TestCase):
    def test_all_positions_after_single_psn_label_are_preserved(self):
        node = ElementTree.fromstring("<notam />")
        text = (
            "E) PSN: 353000N1393000E\n"
            "2) 354000N1394000E\n"
            "3) 355000N1395000E"
        )
        polygons, lines, circles, points = geometry(node, text, None)
        self.assertEqual(polygons, [])
        self.assertEqual(lines, [])
        self.assertEqual(circles, [])
        self.assertEqual(len(points), 3)

    def test_independent_point_is_preserved_with_line_geometry(self):
        node = ElementTree.fromstring("<notam />")
        text = (
            "E) LINE CONNECTING 353000N1393000E TO 354000N1394000E\n"
            "PSN: 355000N1395000E"
        )
        polygons, lines, circles, points = geometry(node, text, None)
        self.assertEqual(polygons, [])
        self.assertEqual(len(lines), 1)
        self.assertEqual(circles, [])
        self.assertEqual(len(points), 1)

    def test_polygon_vertices_are_not_duplicated_as_point_icons(self):
        node = ElementTree.fromstring(
            "<notam><posList>35.0 139.0 35.1 139.0 35.1 139.1</posList></notam>"
        )
        text = "E) AREA 350000N1390000E 350600N1390000E 350600N1390600E"
        polygons, lines, circles, points = geometry(node, text, None)
        self.assertEqual(len(polygons), 1)
        self.assertEqual(lines, [])
        self.assertEqual(circles, [])
        self.assertEqual(points, [])

    def test_line_connecting_three_positions_is_one_complete_polyline(self):
        node = ElementTree.fromstring("<notam />")
        text = (
            "E) LINE CONNECTING 353000N1393000E TO 354000N1394000E "
            "TO 355000N1395000E"
        )
        polygons, lines, circles, points = geometry(node, text, None)
        self.assertEqual(polygons, [])
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(lines[0]), 3)
        self.assertEqual(circles, [])
        self.assertEqual(points, [])


if __name__ == "__main__":
    unittest.main()
