"""A tiny in-process stand-in for an ntfy server: JSON publish, raw publish, and the /json stream."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class MockNtfy:
    def __init__(self):
        self.published = []        # JSON publishes (notifications to the phone)
        self.topics = {}           # topic -> [(id, message)]
        self.lock = threading.Lock()
        self.counter = 0
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
                path = urlparse(self.path).path.strip("/")
                with mock.lock:
                    if not path:
                        mock.published.append(json.loads(body))
                    else:
                        mock.counter += 1
                        mock.topics.setdefault(path, []).append((str(mock.counter), body))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"id":"x"}')

            def do_GET(self):
                url = urlparse(self.path)
                topic = url.path.strip("/").rsplit("/json", 1)[0]
                since = parse_qs(url.query).get("since", ["all"])[0]
                poll = parse_qs(url.query).get("poll", [""])[0] == "1"
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                sent = set()
                if since != "all":
                    with mock.lock:
                        for msg_id, _ in mock.topics.get(topic, []):
                            sent.add(msg_id)
                            if msg_id == since:
                                break
                try:
                    while not mock.closing:
                        with mock.lock:
                            pending = [(i, m) for i, m in mock.topics.get(topic, []) if i not in sent]
                        for msg_id, body in pending:
                            sent.add(msg_id)
                            line = json.dumps({"id": msg_id, "event": "message", "topic": topic, "message": body})
                            self.wfile.write(line.encode("utf-8") + b"\n")
                            self.wfile.flush()
                        if poll:
                            return
                        time.sleep(0.05)
                except OSError:
                    pass

        self.closing = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, topic: str, message: str):
        with self.lock:
            self.counter += 1
            self.topics.setdefault(topic, []).append((str(self.counter), message))

    def wait_published(self, count: int = 1, timeout: float = 5) -> list:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if len(self.published) >= count:
                    return list(self.published)
            time.sleep(0.02)
        raise AssertionError(f"expected {count} publish(es), got {len(self.published)}")

    def close(self):
        self.closing = True
        self.server.shutdown()
        self.server.server_close()
