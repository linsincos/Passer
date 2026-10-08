from __future__ import annotations

import unittest

import map_tool


@unittest.skipIf(map_tool.Image is None, "Pillow is required for map tile tests")
class MapZoomTransitionTests(unittest.TestCase):
    def _window(self):
        window = map_tool.MapWindow.__new__(map_tool.MapWindow)
        window.tile_images = {}
        return window

    def test_zoom_in_uses_the_matching_parent_tile_area(self):
        window = self._window()
        parent = map_tool.Image.new("RGB", (256, 256), "red")
        parent.paste(map_tool.Image.new("RGB", (128, 128), "green"), (128, 0))
        parent.paste(map_tool.Image.new("RGB", (128, 128), "blue"), (0, 128))
        parent.paste(map_tool.Image.new("RGB", (128, 128), "yellow"), (128, 128))
        window.tile_images[(3, 2, 1)] = parent

        # (z=4, x=5, y=2) is the upper-right quarter of parent (3, 2, 1).
        fallback = window.fallback_tile_image((4, 5, 2))
        self.assertIsNotNone(fallback)
        self.assertEqual(fallback.size, (256, 256))
        self.assertEqual(fallback.getpixel((128, 128)), (0, 128, 0))

    def test_zoom_out_stitches_cached_children_into_quadrants(self):
        window = self._window()
        colors = {
            (4, 4, 2): "red",
            (4, 5, 2): "green",
            (4, 4, 3): "blue",
            (4, 5, 3): "yellow",
        }
        window.tile_images = {
            key: map_tool.Image.new("RGB", (256, 256), color)
            for key, color in colors.items()
        }

        fallback = window.fallback_tile_image((3, 2, 1))
        self.assertIsNotNone(fallback)
        self.assertEqual(fallback.getpixel((64, 64)), (255, 0, 0))
        self.assertEqual(fallback.getpixel((192, 64)), (0, 128, 0))
        self.assertEqual(fallback.getpixel((64, 192)), (0, 0, 255))
        self.assertEqual(fallback.getpixel((192, 192)), (255, 255, 0))

    def test_no_cached_neighbouring_level_returns_no_placeholder_image(self):
        window = self._window()
        self.assertIsNone(window.fallback_tile_image((10, 511, 340)))


if __name__ == "__main__":
    unittest.main()
