from __future__ import annotations

import unittest
from unittest import mock

import calculator_tool
import math_render


def _only_child(text: str):
    tree = math_render._parse(text)
    assert tree[0] == "row" and len(tree[1]) == 1
    return tree[1][0]


class NaturalDisplayParseTests(unittest.TestCase):
    def test_fraction_groups_are_structural_not_visible_parentheses(self):
        node = _only_child("(1+2)/(3+4)")
        self.assertEqual(node[0], "frac")
        self.assertEqual(node[1][0], "row")
        self.assertEqual(node[2][0], "row")

    def test_power_group_is_a_real_superscript(self):
        node = _only_child("x^(2+3)")
        self.assertEqual(node[0], "sup")
        self.assertEqual(node[2][0], "row")

    def test_nth_root_and_log_base_have_dedicated_2d_nodes(self):
        self.assertEqual(_only_child("root(3,x+1)")[0], "nroot")
        self.assertEqual(_only_child("log_2(x+1)")[0], "logbase")

    def test_calculus_and_big_operators_have_dedicated_2d_nodes(self):
        integral = _only_child("∫_0^1(x^2)d[x]")
        derivative = _only_child("d/d[x](x^2)|x=3")
        summation = _only_child("summation(n^2,(n,1,10))")
        product = _only_child("product(n,(n,1,5))")
        self.assertEqual(integral[0], "integral")
        self.assertEqual(derivative[0], "derivative")
        self.assertEqual(summation[:2], ("bigop", "Σ"))
        self.assertEqual(product[:2], ("bigop", "Π"))

    def test_nth_root_remains_calculable(self):
        self.assertEqual(calculator_tool.safe_eval_expression("root(3,8)"), 2.0)
        self.assertTrue(calculator_tool.symbolic_calculate("root(3,8)").startswith("2"))

    def test_unfinished_required_parameters_remain_visible_as_boxes(self):
        cursor = math_render.CURSOR_MARK
        sin_node = math_render._parse("sin(" + cursor)[1][1]
        power_node = _only_child("x^" + cursor)
        fraction_node = _only_child("1/" + cursor)
        empty_root = _only_child("root(,2)")
        self.assertEqual(sin_node[0], "paren")
        self.assertEqual(sin_node[1][0], "slot")
        self.assertEqual(power_node[2][0], "slot")
        self.assertEqual(fraction_node[2][0], "slot")
        self.assertEqual(empty_root[1], ("row", [("box",)]))


class ReplayDirectionTests(unittest.TestCase):
    def _window(self):
        window = calculator_tool.CalculatorWindow.__new__(calculator_tool.CalculatorWindow)
        window.display = mock.Mock()
        window.math_canvas = object()
        window.expression_var = mock.Mock()
        window.expression_var.get.return_value = "(1)/(2)"
        window._cursor = 5
        window._math_scroll_x = 0.0
        window._move_cursor = mock.Mock()
        window._set_cursor = mock.Mock()
        window.recall_history = mock.Mock()
        return window

    def test_left_and_right_buttons_move_the_formula_cursor(self):
        window = self._window()
        window._navigate_replay("left")
        window._navigate_replay("right")
        self.assertEqual(window._move_cursor.call_args_list, [mock.call(-1, select=False),
                                                              mock.call(1, select=False)])

    def test_vertical_buttons_prefer_2d_navigation_then_history(self):
        window = self._window()
        with mock.patch.object(math_render, "move_caret_2d", return_value=2):
            window._navigate_replay("up")
        window._set_cursor.assert_called_once_with(2, select=False)
        window.recall_history.assert_not_called()

        window._set_cursor.reset_mock()
        with mock.patch.object(math_render, "move_caret_2d", return_value=5):
            window._navigate_replay("down")
        window._set_cursor.assert_not_called()
        window.recall_history.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
