from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import Passer
import mail_tool


class _Root:
    def __init__(self) -> None:
        self.calls = []
        self.callbacks = []
        self.current_state = "normal"
        self.bindings = {}

    def bind(self, event, callback, **kwargs):
        self.bindings[event] = callback

    def after(self, delay, callback):
        self.calls.append(("after", delay))
        self.callbacks.append(callback)
        return f"after-{len(self.callbacks)}"

    def after_cancel(self, after_id):
        self.calls.append(("after_cancel", after_id))

    def after_idle(self, callback):
        return self.after(0, callback)

    def focus_get(self):
        return None

    def state(self):
        return self.current_state

    def overrideredirect(self, value):
        self.calls.append(("override", value))

    def deiconify(self):
        self.calls.append("deiconify")
        self.current_state = "normal"


class WindowMinimizeTests(unittest.TestCase):
    def test_tool_and_owned_window_release_does_not_undo_hide_or_minimize(self):
        for registration in ("tool", "owned"):
            with self.subTest(registration=registration):
                app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
                app.root = _Root()
                manager = Passer.PasserFocusManager(app.root)
                app.focus_manager = manager
                window = _Root()
                with (mock.patch.object(manager, "activate", side_effect=lambda w: w.deiconify()) as activate,
                      mock.patch.object(manager, "_is_foreground", return_value=True),
                      mock.patch.object(manager, "editable_target", return_value=None)):
                    if registration == "tool":
                        app.place_tool_window_on_passer(SimpleNamespace(window=window))
                    else:
                        app.keep_window_above_main(window)
                    release = window.bindings["<ButtonRelease-1>"]
                    activate.reset_mock()
                    for state in ("iconic", "withdrawn"):
                        window.current_state = state
                        release(SimpleNamespace(widget=object(), type=Passer.tk.EventType.ButtonRelease))
                        self.assertEqual(window.state(), state)
                        activate.assert_not_called()
                    window.current_state = "normal"
                    release(SimpleNamespace(widget=object(), type=Passer.tk.EventType.ButtonRelease))
                    activate.assert_called_once_with(window)

    def test_click_release_preserves_new_dialog_focus(self):
        for registration in ("tool", "owned", "main"):
            with self.subTest(registration=registration):
                app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
                app.root = _Root()
                app._main_minimize_in_progress = False
                manager = Passer.PasserFocusManager(app.root)
                app.focus_manager = manager
                window = app.root if registration == "main" else _Root()
                new_dialog = _Root()
                dialog_entry = SimpleNamespace(winfo_toplevel=lambda: new_dialog)
                with (mock.patch.object(manager, "activate") as activate,
                      mock.patch.object(manager, "_is_foreground", return_value=True) as foreground,
                      mock.patch.object(manager, "claim") as claim,
                      mock.patch.object(manager, "editable_target", return_value=None),
                      mock.patch.object(app, "_restore_main_input_focus_from_click") as restore_input):
                    if registration == "tool":
                        app.place_tool_window_on_passer(SimpleNamespace(window=window))
                    elif registration == "owned":
                        app.keep_window_above_main(window)
                    if registration == "main":
                        press = release = app.activate_main_window
                    else:
                        press = window.bindings["<ButtonPress-1>"]
                        release = window.bindings["<ButtonRelease-1>"]
                    activate.reset_mock()
                    press(SimpleNamespace(widget=object(), type=Passer.tk.EventType.ButtonPress))
                    activate.assert_called_once_with(window)
                    activate.reset_mock()
                    restore_input.reset_mock()

                    # A compose/account command focuses a new dialog before the
                    # owner's toplevel release handler gets the same click.
                    window.focus_get = lambda: dialog_entry
                    release(SimpleNamespace(widget=object(), type=Passer.tk.EventType.ButtonRelease))
                    activate.assert_not_called()
                    claim.assert_not_called()
                    restore_input.assert_not_called()

                    # A native dialog/external app may not be visible to Tk.
                    window.focus_get = lambda: None
                    foreground.return_value = False
                    release(SimpleNamespace(widget=object(), type=Passer.tk.EventType.ButtonRelease))
                    activate.assert_not_called()

    def test_click_press_reactivates_editor_after_other_window_had_focus(self):
        root = _Root()
        manager = Passer.PasserFocusManager(root)
        target = object()
        previous_window = _Root()
        root.focus_get = lambda: SimpleNamespace(winfo_toplevel=lambda: previous_window)
        with (mock.patch.object(manager, "activate") as activate,
              mock.patch.object(manager, "_is_foreground", return_value=False),
              mock.patch.object(manager, "editable_target", return_value=target),
              mock.patch.object(manager, "claim") as claim):
            manager.activate_from_click(
                root, SimpleNamespace(widget=target, type=Passer.tk.EventType.ButtonPress))
            activate.assert_called_once_with(root)
            claim.assert_called_once_with(root, target, activate=False)

    def test_delayed_focus_and_pointer_recovery_respect_switch_to_other_app(self):
        root = _Root()
        manager = Passer.PasserFocusManager(root)
        target = mock.Mock()
        target.winfo_exists.return_value = True
        with (mock.patch.object(Passer.sys, "platform", "test"),
              mock.patch.object(manager, "activate") as activate,
              mock.patch.object(manager, "neutralize_ime"),
              mock.patch.object(manager, "_is_foreground", return_value=True) as foreground):
            manager.claim(root, target, activate=False)
            manager.recover_from_pointer(root, resolver=lambda: target)
            target.reset_mock()
            foreground.return_value = False
            callbacks, root.callbacks = root.callbacks, []
            for callback in callbacks:
                callback()
            activate.assert_not_called()
            target.focus_set.assert_not_called()
            target.focus_force.assert_not_called()
            self.assertEqual(root.callbacks, [])
            foreground.return_value = True
            manager.claim(root, target, activate=False)
            target.focus_set.assert_called_once()

    def test_rapid_restore_reinstates_main_and_mail_custom_frames(self):
        for kind in ("main", "mail"):
            with self.subTest(kind=kind):
                root = _Root()
                def minimize(window):
                    window.overrideredirect(False)
                    window.current_state = "iconic"
                    return True
                if kind == "main":
                    owner = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
                    owner.root = root
                    owner.focus_manager = mock.Mock()
                    owner._main_minimize_in_progress = False
                    owner._main_frame_restore_after_id = None
                    action = owner.minimize_window
                    on_map = owner.restore_custom_frame
                    module = Passer
                else:
                    owner = mail_tool.MailWindow.__new__(mail_tool.MailWindow)
                    owner.app = SimpleNamespace(focus_manager=mock.Mock())
                    owner.window = root
                    owner._minimize_in_progress = False
                    owner._frame_restore_after_id = None
                    action = owner.minimize
                    on_map = owner._restore_custom_frame
                    module = mail_tool
                with mock.patch.object(module, "minimize_frameless_window", side_effect=minimize):
                    action()
                root.current_state = "normal"
                on_map(SimpleNamespace(widget=root))
                while root.callbacks:
                    root.callbacks.pop(0)()
                self.assertIn(("override", True), root.calls)

    def test_pending_editor_retries_do_not_undo_minimize(self):
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app.root = _Root()
        app._main_minimize_in_progress = False
        app._main_frame_restore_after_id = None
        manager = Passer.PasserFocusManager(app.root)
        app.focus_manager = manager
        target = mock.Mock()
        target.winfo_exists.return_value = True

        def minimize(root):
            root.current_state = "iconic"
            return True

        with (mock.patch.object(manager, "activate") as activate,
              mock.patch.object(manager, "_is_foreground", return_value=False),
              mock.patch.object(manager, "neutralize_ime"),
              mock.patch.object(Passer.sys, "platform", "test"),
              mock.patch.object(Passer, "minimize_frameless_window", side_effect=minimize)):
            manager.claim(app.root, target, activate=False)
            manager.recover_from_pointer(app.root, resolver=lambda: target)
            activate.reset_mock()
            target.reset_mock()
            app.minimize_window()
            # Run the transition completion before all stale 180 ms focus retries.
            app.root.callbacks.pop()()
            callbacks, app.root.callbacks = app.root.callbacks, []
            for callback in callbacks:
                callback()
            app.activate_main_window(SimpleNamespace(widget=target))
            app.ensure_window_visible()
            self.assertEqual(app.root.state(), "iconic")
            self.assertNotIn("deiconify", app.root.calls)
            activate.assert_not_called()
            target.focus_set.assert_not_called()
            self.assertEqual(app.root.callbacks, [])

            # A deliberate restore still allows new input focus requests.
            app.root.current_state = "normal"
            manager.claim(app.root, target, activate=False)
            target.focus_set.assert_called_once()

    def test_recovery_does_not_restore_minimized_window_or_owner(self):
        root = _Root()
        window = _Root()
        manager = Passer.PasserFocusManager(root)
        target = mock.Mock()
        with mock.patch.object(manager, "claim") as claim:
            for minimized in (root, window):
                manager.recover_from_pointer(window, resolver=lambda: target)
                minimized.current_state = "iconic"
                callbacks, window.callbacks = window.callbacks, []
                for callback in callbacks:
                    callback()
                claim.assert_not_called()
                minimized.current_state = "normal"

    def test_cancelled_recovery_stays_cancelled_after_restore(self):
        root = _Root()
        manager = Passer.PasserFocusManager(root)
        with mock.patch.object(manager, "claim") as claim:
            manager.recover_from_pointer(root, resolver=lambda: object())
            manager.cancel_pending(root)
            for callback in root.callbacks:
                callback()
            claim.assert_not_called()

    def test_main_minimize_blocks_same_click_reactivation(self):
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app.root = _Root()
        app.focus_manager = mock.Mock()
        app._main_minimize_in_progress = False
        app._main_frame_restore_after_id = None

        with mock.patch.object(Passer, "minimize_frameless_window", return_value=True) as minimize:
            app.minimize_window()
            app.activate_main_window(SimpleNamespace(widget=object()))

        minimize.assert_called_once_with(app.root)
        self.assertTrue(app._main_minimize_in_progress)
        self.assertNotIn("deiconify", app.root.calls)
        app.focus_manager.activate.assert_not_called()

        app.root.callbacks.pop(0)()
        self.assertFalse(app._main_minimize_in_progress)

    def test_frame_restore_rechecks_state_and_ignores_child_map_events(self):
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app.root = _Root()
        app._main_minimize_in_progress = False
        app._main_frame_restore_after_id = None

        app.restore_custom_frame(SimpleNamespace(widget=object()))
        self.assertEqual(app.root.callbacks, [])

        app.restore_custom_frame(SimpleNamespace(widget=app.root))
        self.assertEqual(len(app.root.callbacks), 1)
        app.root.current_state = "iconic"
        app.root.callbacks.pop(0)()
        self.assertNotIn(("override", True), app.root.calls)

    def test_mail_minimize_uses_real_frameless_minimize_not_withdraw(self):
        window = mail_tool.MailWindow.__new__(mail_tool.MailWindow)
        window.app = SimpleNamespace(focus_manager=mock.Mock())
        window.window = _Root()
        window._minimize_in_progress = False
        window._frame_restore_after_id = None

        with mock.patch.object(mail_tool, "minimize_frameless_window", return_value=True) as minimize:
            window.minimize()

        minimize.assert_called_once_with(window.window)
        self.assertTrue(window._minimize_in_progress)
        self.assertFalse(hasattr(window.window, "withdraw"))
        window.window.callbacks.pop(0)()
        self.assertFalse(window._minimize_in_progress)


if __name__ == "__main__":
    unittest.main()
