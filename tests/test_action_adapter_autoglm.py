import unittest

from phoneharness.tools.gui.action_adapter import parse_autoglm_action


class AutoGLMActionAdapterTest(unittest.TestCase):
    def test_parses_native_tap_answer(self):
        action = parse_autoglm_action(
            '<think>需要点击搜索框。</think><answer>do(action="Tap", element=[512, 188])</answer>'
        )

        self.assertEqual(action.action_type, "click")
        self.assertEqual((action.x, action.y), (512, 188))

    def test_parses_bare_native_action_at_tail(self):
        action = parse_autoglm_action(
            '当前在桌面，需要先打开目标应用。\n'
            'do(action="Launch", app="爱奇艺")'
        )

        self.assertEqual(action.action_type, "open_app")
        self.assertEqual(action.text, "爱奇艺")

    def test_parses_native_launch_type_wait_and_finish(self):
        cases = [
            ('<answer>do(action="Launch", app="美图秀秀")</answer>', ("open_app", "美图秀秀")),
            ('<answer>do(action="Type", text="红烧肉的家常做法")</answer>', ("type", "红烧肉的家常做法")),
            ('<answer>do(action="Wait", duration="2 seconds")</answer>', ("wait", "")),
            ('<answer>finish(message="已完成")</answer>', ("finished", "已完成")),
        ]

        for response, expected in cases:
            with self.subTest(response=response):
                action = parse_autoglm_action(response)
                self.assertEqual(action.action_type, expected[0])
                self.assertEqual(action.text, expected[1])

    def test_parses_native_back_and_home(self):
        back = parse_autoglm_action('<answer>do(action="Back")</answer>')
        home = parse_autoglm_action('<answer>do(action="Home")</answer>')

        self.assertEqual(back.action_type, "press_back")
        self.assertEqual(home.action_type, "press_home")

    def test_still_supports_hy_sft_xml_fallback(self):
        action = parse_autoglm_action(
            "<tool_calls>\n"
            "<tool_call>click<tool_sep>\n"
            "<arg_key>points</arg_key>\n"
            "<arg_value>[[320, 640]]</arg_value>\n"
            "</tool_call>\n"
            "</tool_calls>"
        )

        self.assertEqual(action.action_type, "click")
        self.assertEqual((action.x, action.y), (320, 640))


if __name__ == "__main__":
    unittest.main()
