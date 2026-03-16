#!/usr/bin/env python3
import os, json, subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler

os.environ['DISPLAY'] = ':1'

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        if self.path == '/navigate':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            url = body.get('url', '')
            if url:
                env = {**os.environ, 'DISPLAY': ':1'}
                subprocess.run(['xdotool', 'key', 'ctrl+l'], env=env)
                subprocess.run(['xdotool', 'sleep', '0.1'], env=env)
                subprocess.run(['xdotool', 'type', '--clearmodifiers', '--delay', '0', url], env=env)
                subprocess.run(['xdotool', 'key', 'Return'], env=env)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({'ok': True, 'url': url}).encode())
        else:
            self.send_response(404)
            self.end_headers()

print('Nav API listening on :6081')
HTTPServer(('0.0.0.0', 6081), Handler).serve_forever()
