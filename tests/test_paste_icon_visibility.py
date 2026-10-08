from __future__ import annotations

import unittest
from types import SimpleNamespace

import Passer


class _FakeRoot:
    @staticmethod
    def state() -> str:
        return "normal"


class _FakeCanvas:
    @staticmethod
    def winfo_ismapped() -> bool:
        return True

    @staticmethod
    def canvasx(_value: int) -> float:
        return 0.0

    @staticmethod
    def canvasy(_value: int) -> float:
        return 0.0

    @staticmethod
    def winfo_width() -> int:
        return 400

    @staticmethod
    def winfo_height() -> int:
        return 400


class _FakeTile:
    def __init__(self, manager: str) -> None:
        self.manager = manager
        self.place_calls: list[dict] = []
        self.forget_calls = 0

    def winfo_manager(self) -> str:
        return self.manager

    def place(self, **kwargs) -> None:
        self.manager = "place"
        self.place_calls.append(kwargs)

    def place_forget(self) -> None:
        self.manager = ""
        self.forget_calls += 1


class PasteIconVisibilityTests(unittest.TestCase):
    def test_visibility_refresh_restores_pasted_row_without_aira_geometry(self) -> None:
        first_row = SimpleNamespace(id="first")
        below_viewport = SimpleNamespace(id="below")
        first_tile = _FakeTile("")
        below_tile = _FakeTile("place")
        lifted: list[bool] = []

        app = SimpleNamespace(
            _tile_visibility_after_id="pending",
            root=_FakeRoot(),
            canvas=_FakeCanvas(),
            tile_widgets={"first": [first_tile], "below": [below_tile]},
            ai_chat=SimpleNamespace(_lift_widgets=lambda: lifted.append(True)),
            top_level_items=lambda: [first_row, below_viewport],
            item_pixel_position=lambda item: (8, 8) if item.id == "first" else (8, 500),
            _rectangles_overlap=Passer.RelayDockApp._rectangles_overlap,
            _aira_occluder_rects=lambda: (_ for _ in ()).throw(
                AssertionError("Aira screen geometry must not hide item tiles")
            ),
        )

        Passer.RelayDockApp._refresh_tile_visibility(app)

        self.assertIsNone(app._tile_visibility_after_id)
        self.assertEqual(first_tile.manager, "place")
        self.assertEqual(first_tile.place_calls, [
            {"x": 8, "y": 8, "width": Passer.TILE_WIDTH, "height": Passer.TILE_HEIGHT}
        ])
        self.assertEqual(below_tile.manager, "")
        self.assertEqual(below_tile.forget_calls, 1)
        self.assertEqual(lifted, [True])


if __name__ == "__main__":
    unittest.main()
