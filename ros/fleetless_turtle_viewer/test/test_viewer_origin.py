# SPDX-License-Identifier: Apache-2.0
"""Whom the view's WebSocket answers: its own page, and whoever names no origin.

A browser says in `Origin` which page opens a WebSocket, and the page cannot
change that header. The view compares it with `Host`, the place the request
was sent to. Asked three ways here: the comparison alone, the handler
through aiohttp's test client, and the node as a process, for what it logs.
"""

import asyncio
import signal
import subprocess
import time
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer
from ament_index_python.packages import get_package_prefix

from fleetless_turtle_viewer.node import MOST_ORIGINS_NAMED, Hub, build_app, names_this_view

VIEWER = Path(get_package_prefix("fleetless_turtle_viewer")) / "lib/fleetless_turtle_viewer/viewer"
# test_viewer_node.py has 18765 and the one above it.
PORT = 18767

WORLD = '{"type":"world","width":1,"height":1,"resolution":0.1,"kinds":"AA=="}'
REFUSAL = (
    "The view's WebSocket answers its own page only, "
    'and this request names another origin: "{}".\n'
)
# U+017F, LATIN SMALL LETTER LONG S. Written as an escape: in a source file
# it is hard to tell from an f.
LONG_S = "\u017f"
# What every handshake carries besides `Origin` and `Host`.
HANDSHAKE = {
    "Connection": "Upgrade",
    "Upgrade": "websocket",
    "Sec-WebSocket-Version": "13",
    "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
}


# --- The comparison -----------------------------------------------------------


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        ("http://localhost:8765", "localhost:8765"),
        # Docker maps the view to any port; the page's two headers move together.
        ("http://localhost:9000", "localhost:9000"),
        ("http://127.0.0.1:8765", "127.0.0.1:8765"),
        ("http://[::1]:8765", "[::1]:8765"),
        ("http://turtle.example:8765", "turtle.example:8765"),
        # A port left out is the default one of the origin's scheme.
        ("http://localhost", "localhost:80"),
        ("http://localhost:80", "localhost"),
        ("http://localhost", "localhost"),
        ("https://example.org", "example.org:443"),
        # A proxy that ends TLS in front of the view: the scheme is not compared.
        ("https://example.org", "example.org"),
        ("https://example.org:8443", "example.org:8443"),
        # A host name has no case, and neither has a scheme.
        ("http://LOCALHOST:8765", "localhost:8765"),
        ("HTTP://localhost:8765", "LocalHost:8765"),
        ("HTTPS://Example.org", "example.org:443"),
        ("Https://example.org", "example.org"),
        # A name with other letters, as a browser writes it.
        ("http://xn--mnchen-3ya.example:8765", "xn--mnchen-3ya.example:8765"),
    ],
)
def test_the_views_own_page_names_the_view(origin, host):
    assert names_this_view(origin, host)


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        # Another host, another port.
        ("http://evil.example", "localhost:8765"),
        ("http://evil.example:8765", "localhost:8765"),
        ("http://localhost:9000", "localhost:8765"),
        ("http://127.0.0.1:8765", "localhost:8765"),
        ("http://localhost.evil.example:8765", "localhost:8765"),
        ("http://localhost", "localhost:8765"),
        ("https://localhost", "localhost:8765"),
        ("https://localhost", "localhost:80"),
        ("http://localhost", "localhost:443"),
        # What a browser says for a page without an origin: a file, a sandbox.
        ("null", "localhost:8765"),
        # No origin at all.
        ("", "localhost:8765"),
        ("garbage", "localhost:8765"),
        ("localhost:8765", "localhost:8765"),
        ("//localhost:8765", "localhost:8765"),
        ("http://", "localhost:8765"),
        ("http://:8765", ":8765"),
        ("http://localhost:port", "localhost:8765"),
        ("http://localhost:876543", "localhost:876543"),
        ("http://[::1:8765", "[::1:8765"),
        # An origin is a scheme, a host and perhaps a port, and nothing after.
        ("http://localhost:8765/", "localhost:8765"),
        ("http://localhost:8765/page", "localhost:8765"),
        ("http://localhost:8765?x", "localhost:8765"),
        ("http://localhost:8765#x", "localhost:8765"),
        ("http://user@localhost:8765", "localhost:8765"),
        (" http://localhost:8765", "localhost:8765"),
        ("http://local\thost:8765", "localhost:8765"),
        ("http://localhost:8765 http://evil.example", "localhost:8765"),
        ("http://localhost:8765, http://evil.example", "localhost:8765"),
        # A scheme is ASCII. U+017F, the long s, is an `s` only to who
        # ignores case the Unicode way.
        (f"http{LONG_S}://localhost:8765", "localhost:8765"),
        (f"http{LONG_S}://localhost", "localhost:443"),
        (f"HTTP{LONG_S}://localhost", "localhost"),
        # So is a host. No browser sends one that is not, and to who lowers
        # case the Unicode way U+212A, the kelvin sign, is a `k`.
        ("http://m\u00fcnchen.example:8765", "m\u00fcnchen.example:8765"),
        ("http://\u212a.example:8765", "k.example:8765"),
        ("http://k.example:8765", "\u212a.example:8765"),
        ("http://local\u00a0host:8765", "local\u00a0host:8765"),
        # Only a page served over HTTP is the view's.
        ("ftp://localhost:8765", "localhost:8765"),
        ("ws://localhost:8765", "localhost:8765"),
        ("chrome-extension://localhost", "localhost"),
        # The place asked is no place.
        ("http://localhost:8765", None),
        ("http://localhost:8765", ""),
        ("http://localhost:8765", "localhost:8765/"),
        ("http://localhost:8765", "localhost:8765, localhost:8765"),
    ],
)
def test_anything_else_does_not(origin, host):
    assert not names_this_view(origin, host)


# --- The handler, through aiohttp's test client --------------------------------


def ask(tmp_path: Path, question):
    """The answer of `question(client)`, asked of a view that remembers a world."""

    async def asked():
        (tmp_path / "index.html").write_text("<title>turtle-sim</title>\n")
        config = tmp_path / "turtle.fleetless.yaml"
        config.write_text("fleetless: 1\n")
        hub = Hub()
        hub.remembered["world"] = WORLD
        app = build_app(hub, lambda: True, tmp_path, config)
        async with TestClient(TestServer(app)) as client:
            return await question(client)

    return asyncio.run(asked())


def headers(origin: str | None, host: str = "localhost:8765") -> dict[str, str]:
    named = {"Host": host}
    if origin is not None:
        named["Origin"] = origin
    return named


async def first_frame(client: TestClient, origin: str | None, host: str = "localhost:8765") -> str:
    """Connects as a page of `origin` would and returns the first frame it is sent."""
    async with client.ws_connect("/ws", headers=headers(origin, host)) as socket:
        message = await socket.receive(timeout=5.0)
        assert message.type == aiohttp.WSMsgType.TEXT, message
        return message.data


async def refusal(client: TestClient, named) -> tuple[int, str, str]:
    """Status, content type and body of a handshake that is expected to be refused."""
    # A list, not a dict: a request may name a header twice.
    async with client.get("/ws", headers=[*HANDSHAKE.items(), *named]) as response:
        return response.status, response.content_type, await response.text()


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        ("http://localhost:8765", "localhost:8765"),
        ("http://localhost:9000", "localhost:9000"),
        ("http://127.0.0.1:18080", "127.0.0.1:18080"),
        ("http://localhost", "localhost"),
        ("https://turtle.example", "turtle.example"),
    ],
)
def test_the_views_own_page_is_served_on_any_port(tmp_path, origin, host):
    assert ask(tmp_path, lambda client: first_frame(client, origin, host)) == WORLD


def test_a_request_without_an_origin_is_served(tmp_path):
    # No browser: a script, curl, this repository's other tests.
    assert ask(tmp_path, lambda client: first_frame(client, None)) == WORLD


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.example",
        "http://localhost:9000",
        "https://localhost",
        "null",
        "garbage",
        "http://localhost:8765/",
        "",
    ],
)
def test_a_page_of_another_site_is_answered_403_and_one_sentence(tmp_path, origin):
    async def question(client):
        with pytest.raises(aiohttp.WSServerHandshakeError) as refused:
            await first_frame(client, origin)
        return refused.value.status, await refusal(client, headers(origin).items())

    status, answer = ask(tmp_path, question)
    assert status == 403
    assert answer == (403, "text/plain", REFUSAL.format(origin))


def test_two_origins_are_none(tmp_path):
    # One of them is the view's own. Which one would a browser have meant?
    named = [
        ("Host", "localhost:8765"),
        ("Origin", "http://localhost:8765"),
        ("Origin", "http://evil.example"),
    ]
    status, _, body = ask(tmp_path, lambda client: refusal(client, named))
    assert status == 403
    assert body == REFUSAL.format("http://localhost:8765, http://evil.example")


def test_an_origin_without_a_host_to_compare_it_with_is_refused(tmp_path):
    # HTTP/1.0 knows no `Host` header, and aiohttp's client always sends one.
    async def question(client):
        reader, writer = await asyncio.open_connection(client.host, client.port)
        writer.write(b"GET /ws HTTP/1.0\r\nOrigin: http://localhost:8765\r\n\r\n")
        await writer.drain()
        answer = await asyncio.wait_for(reader.read(), timeout=5.0)
        writer.close()
        return answer.decode()

    answer = ask(tmp_path, question)
    assert answer.startswith("HTTP/1.0 403 Forbidden\r\n")
    assert answer.endswith(REFUSAL.format("http://localhost:8765"))


def test_an_origin_with_two_hosts_to_compare_it_with_is_refused(tmp_path):
    # aiohttp's client sends one `Host`, whatever it is handed.
    async def question(client):
        reader, writer = await asyncio.open_connection(client.host, client.port)
        writer.write(
            b"GET /ws HTTP/1.1\r\n"
            b"Host: localhost:8765\r\n"
            b"Host: localhost:8765\r\n"
            b"Origin: http://localhost:8765\r\n"
            b"Connection: close\r\n\r\n"
        )
        await writer.drain()
        answer = await asyncio.wait_for(reader.read(), timeout=5.0)
        writer.close()
        return answer.decode()

    answer = ask(tmp_path, question)
    assert answer.startswith("HTTP/1.1 403 Forbidden\r\n")
    assert answer.endswith(REFUSAL.format("http://localhost:8765"))


def test_a_scheme_that_only_looks_like_https_is_refused_like_the_rest(tmp_path, capfd, caplog):
    # It is no origin, so it gets the 403 and the one sentence. It is not
    # for the server to answer 500 and write a stack trace per request.
    origin = f"http{LONG_S}://localhost:8765"

    async def question(client):
        with pytest.raises(aiohttp.WSServerHandshakeError) as refused:
            await first_frame(client, origin)
        return refused.value.status, [
            await refusal(client, headers(origin).items()) for _ in range(3)
        ]

    status, answers = ask(tmp_path, question)
    assert status == 403
    assert answers == [(403, "text/plain", REFUSAL.format("http\\u017f://localhost:8765"))] * 3
    # aiohttp reports a handler that raised through `logging`, the view
    # reports a refusal on stderr: nothing of the first, one line of the second.
    assert [record.getMessage() for record in caplog.records] == []
    said = warnings(capfd)
    assert len(said) == 1
    assert '"http\\u017f://localhost:8765"' in said[0] and "Traceback" not in said[0]


def test_what_cannot_be_printed_is_escaped_and_what_is_long_is_cut(tmp_path):
    long = "http://" + "a" * 5000 + ".example"
    for origin, shown in [
        ('http://evil.example/"\\', 'http://evil.example/\\"\\\\'),
        ("http://evil.example/ä", "http://evil.example/\\u00e4"),
        (long, long[:200] + "..."),
    ]:
        status, _, body = ask(
            tmp_path, lambda client, origin=origin: refusal(client, headers(origin).items())
        )
        assert status == 403
        assert body == REFUSAL.format(shown)
        assert body.isascii() and len(body) < 400


def test_the_other_routes_answer_a_page_of_another_site_as_before(tmp_path):
    # A browser keeps another site from reading them, and none holds the
    # turtle's state.
    async def question(client):
        statuses = {}
        for path in ["/", "/healthz", "/fleetless.yaml", "/index.html"]:
            async with client.get(path, headers=headers("http://evil.example")) as response:
                statuses[path] = response.status
        return statuses

    assert set(ask(tmp_path, question).values()) == {200}


def warnings(capfd) -> list[str]:
    """The lines the view's logger has written since the last look."""
    return [line for line in capfd.readouterr().err.splitlines() if "[turtle_viewer]" in line]


def test_the_log_names_a_refused_origin_once(tmp_path, capfd):
    async def question(client):
        said = []
        for origin in [
            "http://evil.example",
            "http://evil.example",
            "http://evil.example",
            "http://other.example:8000",
            "http://evil.example",
            "http://localhost:8765",
            None,
        ]:
            if origin in ("http://localhost:8765", None):
                await first_frame(client, origin)
            else:
                await refusal(client, headers(origin).items())
            said.append(warnings(capfd))
        return said

    first, second, third, other, fourth, own, none = ask(tmp_path, question)
    assert len(first) == 1
    assert "[WARN]" in first[0]
    assert first[0].endswith(
        'A page of "http://evil.example" asked for the turtle\'s state at "localhost:8765" '
        "and was refused: the view's WebSocket answers its own page only."
    )
    # A refused page tries again every few seconds. The log has said it.
    assert second == third == fourth == []
    assert len(other) == 1 and '"http://other.example:8000"' in other[0]
    # Whoever is served is nobody's news.
    assert own == none == []


def test_the_log_stops_naming_origins_after_a_hundred(tmp_path, capfd):
    # One site can ask under any number of names.
    async def question(client):
        for number in range(MOST_ORIGINS_NAMED + 20):
            await refusal(client, headers(f"http://evil-{number}.example").items())
        said = warnings(capfd)
        status, _, _ = await refusal(client, headers("http://one-more.example").items())
        return said, status, warnings(capfd)

    said, status, after = ask(tmp_path, question)
    assert MOST_ORIGINS_NAMED == 100
    assert len(said) == 101
    assert '"http://evil-99.example"' in said[99]
    assert said[100].endswith("100 origins were refused: the log names no further ones.")
    # Refused all the same.
    assert status == 403 and after == []


# --- The node as a process -----------------------------------------------------


def test_the_node_refuses_says_so_once_and_goes_on_serving():
    process = subprocess.Popen(
        [str(VIEWER), "--ros-args", "-p", f"port:={PORT}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{PORT}"
    own = {"Host": f"localhost:{PORT}", "Origin": f"http://localhost:{PORT}"}
    foreign = {"Host": f"localhost:{PORT}", "Origin": "http://evil.example"}
    long_s = {"Host": f"localhost:{PORT}", "Origin": f"http{LONG_S}://localhost:{PORT}"}

    async def browse():
        async with aiohttp.ClientSession() as session:
            deadline = time.monotonic() + 30.0
            while True:
                try:
                    # 503 without a turtle on the graph. What counts is an answer.
                    async with session.get(url + "/healthz"):
                        break
                except aiohttp.ClientError:
                    assert time.monotonic() < deadline, "The view never answered."
                    await asyncio.sleep(0.1)
            for named in (foreign, foreign, foreign, long_s, long_s):
                with pytest.raises(aiohttp.WSServerHandshakeError) as refused:
                    await session.ws_connect(url + "/ws", headers=named)
                assert refused.value.status == 403
            # The view goes on serving: its own page, and whoever names no origin.
            for named in (own, {}):
                socket = await session.ws_connect(url + "/ws", headers=named)
                await socket.close()
            async with session.get(url + "/healthz") as response:
                assert response.status in (200, 503)

    try:
        asyncio.run(browse())
        process.send_signal(signal.SIGINT)
        said, _ = process.communicate(timeout=10.0)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == 0, said
    assert "Traceback" not in said, said
    named = [line for line in said.splitlines() if "evil.example" in line]
    assert len(named) == 1, said
    assert "[WARN]" in named[0] and "[turtle_viewer]" in named[0]
    assert named[0].endswith(
        f'A page of "http://evil.example" asked for the turtle\'s state at "localhost:{PORT}" '
        "and was refused: the view's WebSocket answers its own page only."
    )
