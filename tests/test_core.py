from nowplaying.backends.demo import DemoBackend
from nowplaying.backends.linux import FORMAT, SEP, parse_playerctl_line
from nowplaying.models import format_time, pretty_source_name


def test_format_time():
    assert format_time(None) == "--:--"
    assert format_time(65) == "1:05"
    assert format_time(3725) == "1:02:05"


def test_pretty_source_name():
    assert pretty_source_name("Spotify.exe") == "Spotify"
    assert pretty_source_name("chrome") == "Google Chrome"
    assert pretty_source_name("MSEdge") == "Microsoft Edge"
    assert pretty_source_name("firefox.instance_1_42") == "Firefox"


def test_parse_playerctl_line():
    line = SEP.join(["spotify", "spotify", "Playing", "Song", "Artist", "Album",
                     "https://i.scdn.co/x.jpg", "200000000", "42000000"])
    t = parse_playerctl_line(line)
    assert t.title == "Song" and t.artist == "Artist" and t.is_playing
    assert t.duration == 200 and t.position == 42
    assert t.source_name == "Spotify"


def test_parse_playerctl_line_no_length():
    line = SEP.join(["firefox", "firefox.instance_1_2", "Paused", "Video", "", "", "", "", "0"])
    t = parse_playerctl_line(line)
    assert t.source_id == "firefox.instance_1_2"
    assert not t.is_playing and t.duration is None and t.position is None


def test_format_has_all_fields():
    assert FORMAT.count(SEP) == 8


def test_demo_controls():
    b = DemoBackend()
    first = b.get_current().title
    b.next("demo:player")
    assert b.get_current().title != first
    b.previous("demo:player")
    assert b.get_current().title == first
    b.play_pause("demo:player")
    assert not b.get_current().is_playing
