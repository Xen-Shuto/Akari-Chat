"""Launch-only splash; no persistent flags or user data are changed."""
import json
import secrets
import re
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

# Browser-renderable visual media only. SVG/HTML/audio are intentionally excluded.
MEDIA = {'.gif': 'image/gif', '.png': 'image/png', '.jpg': 'image/jpeg',
         '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.avif': 'image/avif',
         '.bmp': 'image/bmp', '.mp4': 'video/mp4', '.webm': 'video/webm',
         '.ogv': 'video/ogg'}


class StartupSplash:
    def __init__(self, web):
        self.web = Path(web)
        self.index = (self.web / 'index.html').read_text('utf-8') if (self.web / 'index.html').is_file() else ''
        self.static = {name.lstrip('/') for name in re.findall(r'(?:src|href)="(/[^"?]+)"', self.index) if name.endswith(('.js','.css'))}
        self.token = secrets.token_urlsafe(24)
        self.claimed = False
        self.asset = next((p for ext in MEDIA for p in sorted(self.web.glob('*'))
                           if p.stem.lower() == 'splash' and p.suffix.lower() == ext
                           and p.is_file() and not p.is_symlink()), None)
        self.initialized = False
        self.engine = None
        self.failed = False

    def status(self):
        state = self.engine.status()['state'] if self.engine else None
        return {'done': self.failed or self.initialized and state not in ('loading', 'starting'),
                'failed': self.failed, 'initialized': self.initialized, 'model_state': state}

    def handle(self, handler):
        path = urlsplit(handler.path)
        if path.path not in ('/startup-splash', '/startup-splash/status',
                             '/startup-splash/media', '/startup_splash.js', '/startup_splash.css'):
            return False
        host = handler.headers.get('Host')
        if host not in (f'127.0.0.1:{handler.server.server_port}', f'localhost:{handler.server.server_port}'):
            self.reply(handler, b'', 'text/plain', 403)
            return True
        if path.path in ('/startup_splash.js', '/startup_splash.css'):
            name = path.path[1:]
            self.reply(handler, (self.web / name).read_bytes(),
                       'text/javascript' if name.endswith('.js') else 'text/css')
            return True
        if parse_qs(path.query).get('token', [''])[0] != self.token:
            self.reply(handler, b'', 'text/plain', 404)
            return True
        if path.path == '/startup-splash':
            if self.claimed:
                handler.send_response(303); handler.send_header('Location', '/')
                handler.send_header('Content-Length', '0'); handler.end_headers()
            else:
                self.claimed = True
                overlay = (self.web / 'startup_splash.html').read_text('utf-8')
                html = self.index.replace('<head>', '<head><link rel="stylesheet" href="/startup_splash.css"><script src="/startup_splash.js" defer></script>', 1).replace('<body>', '<body>' + overlay, 1)
                self.reply(handler, html.encode('utf-8'), 'text/html; charset=utf-8')
        elif path.path.endswith('/status'):
            value = self.status()
            value['media'] = ('video' if MEDIA[self.asset.suffix.lower()].startswith('video/') else 'image') if self.asset else None
            self.reply(handler, json.dumps(value).encode(), 'application/json')
        elif self.asset:
            self.reply(handler, self.asset.read_bytes(), MEDIA[self.asset.suffix.lower()])
        else:
            self.reply(handler, b'', 'text/plain', 404)
        return True

    def static_asset(self,handler):
        name=urlsplit(handler.path).path.lstrip('/')
        if name not in self.static:return False
        if handler.headers.get('Host') not in (f'127.0.0.1:{handler.server.server_port}',f'localhost:{handler.server.server_port}'):
            self.reply(handler,b'','text/plain',403);return True
        path=self.web/name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(self.web.resolve()):return False
        self.reply(handler,path.read_bytes(),'text/javascript' if name.endswith('.js') else 'text/css')
        return True

    @staticmethod
    def reply(handler, data, mime, status=200):
        handler.send_response(status)
        handler.send_header('Content-Type', mime)
        handler.send_header('Content-Length', str(len(data)))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        handler.end_headers()
        handler.wfile.write(data)


class StartupHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        try:
            if not self.server.startup_splash.handle(self) and not self.server.startup_splash.static_asset(self):
                StartupSplash.reply(self, b'Starting', 'text/plain', 503)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
