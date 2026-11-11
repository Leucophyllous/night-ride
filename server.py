#!/usr/bin/env python3
"""無灯火ナイトライド: 静的ファイル配信 + 共有ランキングAPI (標準ライブラリのみ)

使い方:  python server.py [ポート]      (既定 8080)
ランキングは同じフォルダの ranking.json に保存されます。

カテゴリ:
  bike / drive            ゴール方式(1000mのタイム。速い順)
  bike_free / drive_free  フリーラン(到達距離。遠い順)
"""
import json
import os
import socket
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, 'ranking.json')
LOCK = threading.Lock()
MIN_TIME = {'bike': 60.0, 'drive': 55.0}          # 1000mをこれより速く走るのは不可能(改ざん除け)
MAX_SPEED = {'bike': 12.0, 'drive': 15.0}         # フリーランの平均速度の上限(m/s)
CATS = ('bike', 'drive', 'bike_free', 'drive_free')
KEEP = 100


def is_free(cat):
    return cat.endswith('_free')


def sort_key(cat):
    if is_free(cat):
        return lambda x: (-x.get('dist', 0), x.get('time', 0))
    return lambda x: (x.get('time', 0), x.get('near', 0))


def load():
    try:
        with open(DB, encoding='utf-8') as f:
            d = json.load(f)
        return {c: list(d.get(c, [])) for c in CATS}
    except Exception:
        return {c: [] for c in CATS}


def save(d):
    tmp = DB + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, DB)


def clean_name(s):
    s = ''.join(ch for ch in str(s) if ch.isprintable() and ch not in '<>')[:12].strip()
    return s or 'ななし'


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == '/api/ranking':
            cat = parse_qs(u.query).get('mode', ['bike'])[0]
            if cat not in CATS:
                return self._json({'error': 'mode'}, 400)
            with LOCK:
                lst = load()[cat]
            return self._json({'list': lst[:KEEP]})
        if u.path.endswith(('.json', '.py', '.tmp')):   # ランキングDBやサーバー本体は配信しない
            self.send_error(404)
            return
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != '/api/ranking':
            self.send_error(404)
            return
        try:
            n = int(self.headers.get('Content-Length', '0'))
            if n <= 0 or n > 2048:
                raise ValueError('size')
            e = json.loads(self.rfile.read(n))
            cat = e['mode']
            t = float(e['time'])
            near = int(e.get('near', 0))
            if cat not in CATS or not (0 <= near <= 9999) or not (1.0 <= t <= 36000):
                raise ValueError('range')
            ent = {'name': clean_name(e.get('name', '')), 'time': round(t, 2), 'near': near, 'ts': int(time.time())}
            if is_free(cat):
                dist = int(float(e['dist']))
                base = cat[:-5]
                # 距離は時間×平均速度の上限を超えられない(改ざん除け)
                if not (10 <= dist <= 100000) or dist > t * MAX_SPEED[base] + 60:
                    raise ValueError('dist')
                ent['dist'] = dist
            elif t < MIN_TIME[cat]:
                raise ValueError('too fast')
        except Exception:
            return self._json({'error': 'bad request'}, 400)
        key = sort_key(cat)
        with LOCK:
            d = load()
            lst = d[cat]
            i = next((k for k, x in enumerate(lst) if x['name'] == ent['name']), -1)
            best = False
            if i < 0:
                lst.append(ent)
                best = True
            elif key(ent) < key(lst[i]):
                lst[i] = ent
                best = True
            lst.sort(key=key)
            del lst[KEEP:]
            save(d)
            rank = next((k + 1 for k, x in enumerate(lst) if x['name'] == ent['name']), len(lst))
            total = len(lst)
        return self._json({'rank': rank, 'total': total, 'best': best})


class DualStack(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except Exception:
            pass
        super().server_bind()


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    try:
        srv = DualStack(('::', port), Handler)
    except Exception:
        srv = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    print('serving on port %d (ranking db: %s)' % (port, DB), flush=True)
    srv.serve_forever()
