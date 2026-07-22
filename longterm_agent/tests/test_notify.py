"""TelegramNotifier multi-chat delivery.

The API takes ONE chat per sendMessage call; a comma-joined value passed verbatim is a
nonexistent chat and every alert dies silently. These tests pin the split-and-fan-out
behaviour and the "True if anyone got it" success semantics.
"""
from unittest import mock

from core.notify import TelegramNotifier


def _resp(status=200, text="ok"):
    r = mock.Mock()
    r.status_code = status
    r.text = text
    return r


def _notifier(chat_id):
    # token given explicitly so get_settings() defaults never leak into the test
    return TelegramNotifier(token="TOKEN", chat_id=chat_id)


def test_comma_list_fans_out_to_every_chat():
    n = _notifier("111, 222")  # space after the comma is how the value was actually typed
    with mock.patch("core.notify.requests.post", return_value=_resp()) as post:
        assert n.send("hi") is True
    chats = [c.kwargs["json"]["chat_id"] for c in post.call_args_list]
    assert chats == ["111", "222"]  # trimmed, one call per chat


def test_single_chat_still_single_call():
    n = _notifier("111")
    with mock.patch("core.notify.requests.post", return_value=_resp()) as post:
        assert n.send("hi") is True
    assert post.call_count == 1


def test_one_dead_chat_does_not_kill_the_alert():
    n = _notifier("111,222")
    with mock.patch("core.notify.requests.post",
                    side_effect=[_resp(400, "chat not found"), _resp()]):
        assert n.send("hi") is True  # somebody got it


def test_all_chats_dead_reports_failure():
    n = _notifier("111,222")
    with mock.patch("core.notify.requests.post", return_value=_resp(400, "nope")):
        assert n.send("hi") is False


def test_empty_chat_id_is_disabled_noop():
    # an empty explicit chat_id falls through to the settings default, so pin THAT empty
    settings = mock.Mock()
    settings.telegram_creds.return_value = ("TOKEN", "")
    with mock.patch("core.notify.get_settings", return_value=settings):
        n = TelegramNotifier()
    with mock.patch("core.notify.requests.post") as post:
        assert n.send("hi") is False
    post.assert_not_called()
