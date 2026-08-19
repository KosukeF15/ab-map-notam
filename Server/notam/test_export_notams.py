import unittest
from xml.etree import ElementTree

from export_notams import exclusion_circles, geometry, q_line


class GeometryTests(unittest.TestCase):
    def test_q_line_preserves_web_filter_metadata(self):
        parsed = q_line("Q) RJJJ/QWALW/IV/M/W/000/999/3500N13900E180")

        self.assertEqual(parsed["qcode"], "QWALW")
        self.assertEqual(parsed["scope"], "W")
        self.assertEqual(parsed["radius"], 180)

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

    def test_one_radius_applies_to_every_following_center(self):
        node = ElementTree.fromstring("<notam />")
        text = (
            "E) (1)AREA: 362M EITHER SIDE OF A LINE "
            "324123N1285221E - 324204N1285235E - 324230N1285247E\n"
            "(2)AREA: RADIUS 270M CENTER THE FLW POINTS\n"
            "324218N1285239E 324510N1285418E 324608N1285452E\n"
            "WT: 21KG"
        )
        polygons, lines, circles, points = geometry(node, text, None)
        self.assertEqual(polygons, [])
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(lines[0]), 3)
        self.assertEqual(len(circles), 3)
        self.assertTrue(all(abs(circle["radiusNM"] - 270 / 1852) < 0.000001 for circle in circles))
        self.assertEqual(points, [])

    def test_excluded_circle_is_kept_separate_from_primary_geometry(self):
        text = (
            "E) AREA BOUNDED BY FLW POINTS 412500N1412945E - "
            "410440N1412400E - 405008N1412402E\n"
            "EXCLUDING THE OVERLAPPING AIRSPACE BLW 9000FT WI 18NM RADIUS OF\n"
            "404420.20N1404217.77E(MRE)\n"
            "ATC WILL NOT CLEAR NON-PARTICIPATING IFR FLT THRU THIS AREA."
        )
        circles = exclusion_circles(text)
        self.assertEqual(len(circles), 1)
        self.assertEqual(circles[0]["radiusNM"], 18)
        self.assertAlmostEqual(circles[0]["center"]["latitude"], 40.738944, places=6)


if __name__ == "__main__":
    unittest.main()
