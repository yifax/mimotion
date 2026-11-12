import io
import json
import os
import runpy
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch
from urllib.parse import unquote, urlsplit

import requests

from util import push_util


class BarkPushTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://api.day.app/test-device-key"
        self.results = [
            {"user": "test@example.com", "success": True, "msg": "修改步数（5864）[success]"},
            {"user": "second@example.com", "success": False, "msg": "修改步数（0）[error/a?b#c]"}
        ]

    def test_sends_encoded_result_text_even_outside_other_push_hours(self):
        response = Mock(status_code=200)
        response.json.return_value = {"code": 200}
        config = push_util.PushConfig(bark_url=self.url + "/", push_plus_hour="22")
        with patch.object(push_util.requests, "get", return_value=response) as get, \
                patch.object(push_util, "not_in_push_time_range", return_value=True), \
                patch.object(push_util, "push_to_push_plus") as push_plus, \
                patch.object(push_util, "push_to_wechat_webhook") as wechat, \
                patch.object(push_util, "push_to_telegram_bot") as telegram, \
                patch.object(push_util, "format_now", return_value="2026-11-12 09:30:00"), \
                redirect_stdout(io.StringIO()):
            push_util.push_results(self.results, "summary", config)

        get.assert_called_once()
        url = urlsplit(get.call_args.args[0])
        parts = url.path.split("/")
        self.assertEqual(len(parts), 4)
        self.assertEqual(parts[1], "test-device-key")
        self.assertEqual(unquote(parts[2]), "2026-11-12 09:30:00 刷步数通知")
        self.assertEqual(unquote(parts[3]), "修改步数（5864）[success]\n修改步数（0）[error/a?b#c]")
        self.assertEqual(url.query, "")
        self.assertEqual(url.fragment, "")
        self.assertEqual(get.call_args.kwargs, {
            "params": {"group": "mimotion", "ttl": 600},
            "timeout": 15
        })
        push_plus.assert_not_called()
        wechat.assert_not_called()
        telegram.assert_not_called()

    def test_missing_or_disabled_bark_does_not_send(self):
        for url in (None, "", "NO"):
            with self.subTest(url=url), patch.object(push_util.requests, "get") as get, \
                    redirect_stdout(io.StringIO()):
                push_util.push_to_bark(self.results, push_util.PushConfig(bark_url=url))
                get.assert_not_called()

    def test_reports_delivery_errors_without_exposing_device_key(self):
        http_error = Mock(status_code=503)
        service_error = Mock(status_code=200)
        service_error.json.return_value = {"code": 400}
        invalid_json = Mock(status_code=200)
        invalid_json.json.side_effect = ValueError(self.url)
        invalid_shape = Mock(status_code=200)
        invalid_shape.json.return_value = []
        cases = [
            (http_error, None, "HTTP 503"),
            (service_error, None, "服务返回错误码 400"),
            (invalid_json, None, "响应不是有效的JSON"),
            (invalid_shape, None, "响应格式无效"),
            (None, requests.exceptions.Timeout(self.url), "Timeout")
        ]
        for response, error, expected in cases:
            with self.subTest(expected=expected), \
                    patch.object(push_util.requests, "get", return_value=response, side_effect=error), \
                    redirect_stdout(io.StringIO()) as output:
                push_util.push_bark(self.url, "title", "body")
            self.assertIn(expected, output.getvalue())
            self.assertNotIn("Bark推送成功", output.getvalue())
            self.assertNotIn("test-device-key", output.getvalue())

    def test_pushplus_no_disables_delivery_even_with_bark_enabled(self):
        config = push_util.PushConfig(push_plus_token="NO", bark_url=self.url)
        with patch.object(push_util, "push_bark") as bark, \
                patch.object(push_util, "push_plus") as push_plus, \
                redirect_stdout(io.StringIO()):
            push_util.push_results(self.results, "summary", config)
        bark.assert_called_once()
        push_plus.assert_not_called()

    def test_main_supports_secret_overrides_and_config_fallback(self):
        config = {
            "USER": "test@example.com",
            "PWD": "test-password",
            "BARK_URL": "https://api.day.app/config-test-key",
            "PUSH_PLUS_TOKEN": "config-test-token"
        }
        cases = [
            ({"BARK_URL": self.url, "PUSH_PLUS_TOKEN": "NO"}, self.url, "NO"),
            ({"BARK_URL": ""}, config["BARK_URL"], config["PUSH_PLUS_TOKEN"])
        ]
        for overrides, expected_url, expected_token in cases:
            with self.subTest(overrides=overrides), \
                    patch.dict(os.environ, {"CONFIG": json.dumps(config), **overrides}, clear=True), \
                    patch("util.zepp_helper.login_access_token", return_value=(None, "test failure")), \
                    patch.object(push_util, "push_results") as push_results, \
                    patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network call")), \
                    redirect_stdout(io.StringIO()):
                runpy.run_module("main", run_name="__main__")
            push_results.assert_called_once()
            push_config = push_results.call_args.args[2]
            self.assertEqual(push_config.bark_url, expected_url)
            self.assertEqual(push_config.push_plus_token, expected_token)


if __name__ == "__main__":
    unittest.main()
