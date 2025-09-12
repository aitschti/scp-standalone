import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse
import requests
from requests.adapters import HTTPAdapter
import base64
import hashlib
import re
import os
import time
import logging
import argparse

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global session for connection reuse
_global_session = requests.Session()

# Change connection pool size to handle more concurrent requests (default: 10)
adapter = HTTPAdapter(pool_connections=30, pool_maxsize=30)
_global_session.mount('https://', adapter)
_global_session.mount('http://', adapter)

# Headers for browser-like requests
FORWARD_HEADERS = {
    'Referer': 'https://stripchat.com/',
    'Origin': 'https://stripchat.com',
    'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/88.0.4324.182 Safari/537.36",
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Keep-Alive': 'timeout=30, max=1000',
    'DNT': '1',
}

# API Endpoints
API_ENDPOINT_MODEL = "https://stripchat.com/api/front/v2/models/username/{}/cam"

# M3U8 URL Template
M3U8_BASE_URL = "https://edge-hls.doppiocdn.com/hls/{}/master/{}_auto.m3u8"
M3U8_BESTONLY_URL = "https://edge-hls.doppiocdn.com/hls/{}/master/{}.m3u8"

# Global flag for using best quality playlist
_use_best = False

# Tunables
REQUEST_TIMEOUT = 5
MAX_FETCH_RETRIES = 3
CHUNK_SIZE = 64 * 1024

# Global cache for init segments
_init_cache = {}

# Global flag to halt requests after key fault detection
_key_fault_detected = False

# Global variable for the stream M3U8 URL (default if username provided at startup)
_stream_m3u8_url = None

# Global cache for username -> M3U8 URL (with timestamp for TTL)
_username_m3u8_cache = {}
CACHE_TTL_SECONDS = 30  # 30 seconds

# Global cache for decode key (read once at startup)
_decode_key = None

def _get_decode_key():
    """Return the cached decode key (loaded at startup)."""
    return _decode_key

def _pad_b64(s: str) -> str:
    if not s:
        return s
    return s + ("=" * ((4 - len(s) % 4) % 4))

def _mouflon_decrypt_b64(encrypted_b64: str, key: str) -> str:
    if not encrypted_b64:
        return ""
    try:
        data = base64.b64decode(_pad_b64(encrypted_b64))
    except Exception:
        return ""
    hash_bytes = hashlib.sha256(key.encode("utf-8")).digest()
    out = bytearray()
    for i, b in enumerate(data):
        out.append(b ^ hash_bytes[i % len(hash_bytes)])
    try:
        return out.decode("utf-8")
    except Exception:
        return out.decode("latin-1", errors="ignore")

def _is_valid_decrypted_url(url: str) -> bool:
    """Validate if the decrypted URL looks correct (e.g., ends with .mp4 and has part number)."""
    pattern = r'^https://.*\.mp4$'
    return bool(re.match(pattern, url))

def _decode_m3u8_mouflon_files(m3u8_text: str) -> str:
    """Find '#EXT-X-MOUFLON:FILE:<b64>' lines and replace the next media reference 'media.mp4' with decoded filename."""
    if "#EXT-X-MOUFLON" not in m3u8_text:
        return m3u8_text
    lines = m3u8_text.splitlines()
    key = _get_decode_key()
    if key is None:
        logger.error("Skipping decryption due to missing decode key. Encrypted streams will not play.")
        return m3u8_text  # Return original without decoding
    
    invalid_decryptions = 0
    for idx, line in enumerate(lines):
        if line.startswith("#EXT-X-MOUFLON:FILE:"):
            enc = line.split(":", 2)[-1].strip()
            dec = _mouflon_decrypt_b64(enc, key)
            # Find next non-empty line after the tag and replace 'media.mp4' if present
            for j in range(idx + 1, min(len(lines), idx + 6)):
                candidate = lines[j]
                if candidate.strip() == "":
                    continue
                if "media.mp4" in candidate:
                    new_candidate = candidate.replace("media.mp4", dec)
                    # Validate the full constructed URL
                    if not _is_valid_decrypted_url(new_candidate):
                        logger.error(f"Invalid decrypted URL: {new_candidate}. Decode key may be wrong or outdated.")
                        invalid_decryptions += 1
                        continue  # Skip replacing this one
                    if new_candidate != candidate:
                        lines[j] = new_candidate
                    break
    
    if invalid_decryptions > 0:
        logger.error(f"Decryption failed for {invalid_decryptions} segments. Check decode key in key.txt.")
        # If all decryptions fail, return original to avoid broken stream
        if invalid_decryptions == len([l for l in lines if l.startswith("#EXT-X-MOUFLON:FILE:")]):
            logger.error("All decryptions invalid. Returning original m3u8.")
            return m3u8_text
    
    return "\n".join(lines)

def _extract_psch_and_pkey(m3u8_text):
    """Return (psch_version, pkey) from #EXT-X-MOUFLON:PSCH line if present."""
    for line in m3u8_text.splitlines():
        l = line.strip()
        if not l:
            continue
        if l.upper().startswith('#EXT-X-MOUFLON:PSCH'):
            parts = l.split(':', 3)
            version = parts[2].lower() if len(parts) > 2 else ''
            pkey = parts[3] if len(parts) > 3 else ''
            return version, pkey
    return '', ''

def _make_absolute(base, ref):
    return urllib.parse.urljoin(base, ref)

def _fetch_with_retries(url, headers=None, timeout=REQUEST_TIMEOUT, retries=MAX_FETCH_RETRIES, method='GET'):
    """Fetch URL with retries using requests.Session. Returns response or raises exception."""
    last_exc = None
    hdrs = headers or FORWARD_HEADERS
    for attempt in range(1, retries + 1):
        try:
            resp = _global_session.request(method, url, headers=hdrs, timeout=timeout)
            # For non-2xx, raise HTTPError to match original behavior
            resp.raise_for_status()
            return resp
        except requests.exceptions.HTTPError as he:
            # Return the response for caller to inspect status
            return resp # type: ignore
        except requests.exceptions.RequestException as e:
            last_exc = e
            time.sleep(0.2 * attempt)
    if last_exc:
        raise last_exc
    raise requests.exceptions.RequestException("Failed to fetch after multiple attempts")

def _normalize_strip_psch_pkey(url: str) -> str:
    """Return URL with psch/pkey removed from the query for cache lookups."""
    try:
        parsed = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        q.pop('psch', None)
        q.pop('pkey', None)
        new_q = urllib.parse.urlencode({k: v[0] for k, v in q.items()}) if q else ''
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_q, parsed.fragment))
    except Exception:
        return url

class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        # Suppress default logging by doing nothing
        pass

    def handle(self):
        """Override to catch Windows socket errors (10053/10054) during request handling."""
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError) as e:
            # Handle Windows-specific socket errors for client disconnections
            if hasattr(e, 'winerror') and e.winerror in (10054, 10053):
                logger.debug(f"Client disconnected during request handling: {e}")
            else:
                logger.error(f"Unhandled socket error during request handling: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in request handler: {e}")

    def do_HEAD(self):
        """Handle HEAD requests so clients can probe resources (avoid 501)."""
        path = self.path
        qs = urllib.parse.urlparse(path).query
        params = urllib.parse.parse_qs(qs)
        
        if 'url' in params:
            orig = urllib.parse.unquote(params['url'][0])
        elif path == '/' and _stream_m3u8_url:
            orig = _stream_m3u8_url
        elif path.startswith('/') and len(path) > 1:
            username = path[1:]  # Extract username from /username
            orig = fetch_stream_url(username)
            if not orig:
                self.send_response(404)
                self.send_header('Connection', 'close')
                self.end_headers()
                return
        else:
            self.send_response(400)
            self.send_header('Connection', 'close')
            self.end_headers()
            return
        
        # normalized cache hit check (no body needed)
        norm = _normalize_strip_psch_pkey(orig)
        cached = _init_cache.get(orig) or _init_cache.get(norm)
        if cached:
            try:
                self.send_response(200)
                for h, v in cached.get('headers', {}).items():
                    self.send_header(h, v)
                self.send_header('Content-Length', str(len(cached['bytes'])))
                self.send_header('Connection', 'keep-alive')
                self.end_headers()
            except Exception as e:
                logger.error("Error serving cached HEAD for %s: %s" % (orig, e))
            return

        # build upstream headers to forward
        upstream_headers = dict(FORWARD_HEADERS)
        for hdr in ('Range', 'User-Agent', 'Accept', 'Accept-Encoding', 'Referer', 'Origin', 'If-None-Match', 'If-Modified-Since', 'Cookie'):
            v = self.headers.get(hdr)
            if v:
                upstream_headers[hdr] = v

        # try HEAD first, fall back to GET but do not read body
        try:
            resp = _fetch_with_retries(orig, headers=upstream_headers, method='HEAD')
        except Exception as e:
            try:
                self.send_response(502)
                self.send_header('Connection', 'close')
                self.end_headers()
            except Exception:
                pass
            logger.error("HEAD probe failed for %s: %s" % (orig, e))
            return

        # Handle response
        try:
            status = resp.status_code
        except Exception:
            status = 200
        try:
            self.send_response(status)
            for h in ('Content-Type','Content-Length','Content-Range','Accept-Ranges','Transfer-Encoding','Content-Encoding','Cache-Control','ETag','Set-Cookie'):
                v = resp.headers.get(h)
                if v:
                    self.send_header(h, v)
            self.send_header('Connection', 'close')
            self.end_headers()
        except Exception as e:
            logger.error("Error forwarding HEAD response for %s: %s" % (orig, e))
        return

    def do_GET(self):
        path = self.path
        qs = urllib.parse.urlparse(path).query
        params = urllib.parse.parse_qs(qs)
        
        if 'url' in params:
            orig = urllib.parse.unquote(params['url'][0])
        elif path == '/' and _stream_m3u8_url:
            orig = _stream_m3u8_url
        elif path.startswith('/') and len(path) > 1:
            username = path[1:]  # Extract username from /username
            logger.info(f"New connection request for username: {username}")
            orig = fetch_stream_url(username)
            if not orig:
                self.send_response(404)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    self.wfile.write(b'Stream not found or offline')
                except Exception:
                    pass
                return
            # Log the proxy URL for the master playlist
            host, port = self.server.server_address # type: ignore
            proxy_url = f"http://{host}:{port}/?url={urllib.parse.quote(orig)}"
            logger.info(f"Proxy URL for {username}: {proxy_url}")            
        else:
            self.send_response(400)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Connection', 'close')
            self.end_headers()
            try:
                self.wfile.write(b'No url parameter or invalid path')
            except Exception:
                pass
            return

        # Early halt if key fault detected and this is a segment request
        global _key_fault_detected
        is_playlist = orig.endswith('.m3u8')
        if _key_fault_detected and not is_playlist:
            logger.error("Key fault detected, halting segment request: %s" % orig)
            self.send_response(403)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Connection', 'close')
            self.end_headers()
            try:
                self.wfile.write(b'Decode key error: Playback halted due to invalid key. Check key.txt.')
            except Exception:
                pass
            return

        # Reset flag on playlist request (allow recovery)
        if is_playlist:
            _key_fault_detected = False

        # normalized incoming URL and check init cache (exact or normalized key)
        norm = _normalize_strip_psch_pkey(orig)
        cached = _init_cache.get(orig) or _init_cache.get(norm)
        if cached:
            try:
                self.send_response(200)
                for h, v in cached.get('headers', {}).items():
                    self.send_header(h, v)
                self.send_header('Content-Length', str(len(cached['bytes'])))
                self.send_header('Connection', 'keep-alive')
                self.end_headers()
                self.wfile.write(cached['bytes'])
            except Exception as e:
                pass
            return

        logger.debug("Incoming request for: %s -> orig: %s" % (self.path, orig))

        # Build upstream headers and forward important client headers
        upstream_headers = dict(FORWARD_HEADERS)
        for hdr in ('Range', 'User-Agent', 'Accept', 'Accept-Encoding', 'Referer', 'Origin', 'If-None-Match', 'If-Modified-Since', 'Cookie'):
            v = self.headers.get(hdr)
            if v:
                upstream_headers[hdr] = v

        try:
            resp = _fetch_with_retries(orig, headers=upstream_headers, method='GET')
        except Exception as e:
            self.send_response(502)
            self.send_header('Connection', 'close')
            self.end_headers()
            try:
                self.wfile.write(("Proxy fetch failure: %s" % str(e)).encode('utf-8'))
            except Exception:
                pass
            logger.error("Proxy fetch final failure for %s: %s" % (orig, e))
            return

        # Check for 418 error (indicates invalid segment URL, likely due to wrong key)
        if resp.status_code == 418:
            logger.error(f"Upstream returned 418 (invalid segment URL) for {orig}. Decode key may be wrong or outdated.")
            _key_fault_detected = True  # Set flag to halt further segment requests
            # For playlists, return custom m3u8 to minimize error dialog
            if is_playlist:
                custom_playlist = "#EXTM3U\n#EXT-X-VERSION:3\n# Decode key error: Check key.txt for the correct key.\n"
                body = custom_playlist.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Connection', 'keep-alive')
                self.end_headers()
                self.wfile.write(body)
                return
            # For segments, return 403
            self.send_response(403)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Connection', 'close')
            self.end_headers()
            try:
                self.wfile.write(b'Decode key error: Invalid segment URL. Check key.txt for the correct key.')
            except Exception:
                pass
            return

        # Pass through other HTTPError statuses (using status_code instead of isinstance)
        if resp.status_code >= 400:
            self.send_response(resp.status_code)
            self.send_header('Connection', 'close')
            self.end_headers()
            return

        try:
            content_type = resp.headers.get('Content-Type', '')
        except Exception:
            content_type = ''

        is_playlist = orig.endswith('.m3u8') or content_type in (
            'application/vnd.apple.mpegurl', 'application/x-mpegURL', 'text/plain'
        )

        # Playlist path (rewrite LL-HLS attribute URIs and plain URLs, inject psch/pkey)
        if is_playlist:
            try:
                text = resp.text  # requests handles decompression
                text = _decode_m3u8_mouflon_files(text)
                psch, pkey = _extract_psch_and_pkey(text)
                host, port = self.server.server_address # type: ignore

                def _inject_and_proxy(abs_url: str) -> str:
                    # no longer force playlistType=web
                    pr = urllib.parse.urlsplit(abs_url)
                    q = urllib.parse.parse_qs(pr.query, keep_blank_values=True)
                    if psch and 'psch' not in q:
                        q['psch'] = [psch]
                    if pkey and 'pkey' not in q:
                        q['pkey'] = [pkey]
                    new_q = urllib.parse.urlencode({k: v[0] for k, v in q.items()})
                    abs2 = urllib.parse.urlunsplit((pr.scheme, pr.netloc, pr.path, new_q, pr.fragment))
                    return f'http://{host}:{port}/?url=' + urllib.parse.quote(abs2, safe='')

                def _rewrite_uri_attr(line: str) -> str:
                    m = re.search(r'URI=(?:"([^"]+)"|([^,]+))', line, flags=re.IGNORECASE)
                    if not m:
                        return line
                    uri = (m.group(1) or m.group(2) or '').strip()
                    if not uri:
                        return line
                    absu = _make_absolute(orig, uri)
                    prox = _inject_and_proxy(absu)
                    return re.sub(r'URI=(?:"[^"]+"|[^,]+)', f'URI="{prox}"', line, flags=re.IGNORECASE)

                out = []
                for line in text.splitlines():
                    s = line.strip()
                    u = s.upper()
                    # Rewrite all attribute-URI tags including audio renditions
                    if (u.startswith('#EXT-X-MEDIA') or
                        u.startswith('#EXT-X-I-FRAME-STREAM-INF') or
                        u.startswith('#EXT-X-MAP') or
                        u.startswith('#EXT-X-KEY') or
                        u.startswith('#EXT-X-PART') or
                        u.startswith('#EXT-X-PRELOAD-HINT') or
                        u.startswith('#EXT-X-RENDITION-REPORT')):
                        out.append(_rewrite_uri_attr(line))
                        continue

                    # Rewrite plain URL lines (variants or segments)
                    if s and not s.startswith('#'):
                        absu = _make_absolute(orig, s)
                        out.append(_inject_and_proxy(absu))
                        continue

                    out.append(line)

                body = ("\n".join(out) + "\n").encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Connection', 'keep-alive')
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError) as e:
                    logger.debug("Client disconnected during playlist response for %s: %s" % (orig, e))
                except OSError as e:
                    if hasattr(e, 'winerror') and e.winerror == 10053:
                        logger.debug("Client disconnected during playlist response for %s: %s" % (orig, e))
                    else:
                        raise
                return
            except Exception as e:
                self.send_response(502)
                self.send_header('Connection', 'close')
                self.end_headers()
                logger.error("Error processing playlist response for %s: %s" % (orig, e))
                return

        # Binary/segment path (supports ranges)
        upstream_status = resp.status_code
        try:
            self.send_response(upstream_status)
        except Exception:
            self.send_response(200)
        for h in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges', 'ETag', 'Last-Modified', 'Cache-Control'):
            v = resp.headers.get(h)
            if v:
                self.send_header(h, v)
        te = resp.headers.get('Transfer-Encoding')
        if te:
            self.send_header('Transfer-Encoding', te)
        ce = resp.headers.get('Content-Encoding')
        if ce:
            self.send_header('Content-Encoding', ce)
        self.send_header('Connection', 'keep-alive')
        self.end_headers()

        # Streaming logic
        first = True
        try:
            for chunk in resp.iter_content(CHUNK_SIZE):
                if not chunk:
                    continue
                if first:
                    if b'ftyp' in chunk or b'moov' in chunk or b'sidx' in chunk:
                        logger.debug("Atoms seen in first chunk from %s" % orig)
                    first = False
                self.wfile.write(chunk)
            return
        except (BrokenPipeError, ConnectionResetError) as e:
            # Client disconnected (common with Kodi/VLC); log at debug to reduce noise
            logger.debug("Client disconnected during streaming for %s: %s" % (orig, e))
            return
        except OSError as e:
            # Handle Windows-specific socket errors (10054: remote close, 10053: local abort)
            if hasattr(e, 'winerror') and e.winerror in (10054, 10053):
                logger.debug("Client disconnected during streaming for %s: %s" % (orig, e))
                return
            else:
                # Re-raise other OSError (e.g., network issues)
                raise
        except Exception as e:
            try:
                self.send_response(502)
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(("Proxy processing error: %s" % str(e)).encode('utf-8'))
            except Exception:
                pass
            logger.error("Error processing response for %s: %s" % (orig, e))
            return

class HLSProxy:
    def __init__(self, host='127.0.0.1', port=0):
        self.host = host
        self.port = int(port) if port is not None else 0
        self._server = None
        self._thread = None
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self._server:
                return (self.host, self._server.server_address[1])
            server = ThreadingHTTPServer((self.host, self.port), _ProxyHandler)
            self._server = server
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            self._thread = t
            logger.info("HLS proxy started on %s:%d" % (self.host, server.server_address[1]))
            return (self.host, server.server_address[1])

    def stop(self):
        with self._lock:
            if not self._server:
                return
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
            self._thread = None
            logger.info("HLS proxy stopped")

    def get_local_url(self, original_url):
        host, port = self.start()
        return f'http://{host}:{port}/?url=' + urllib.parse.quote(original_url, safe='')

# singleton
_proxy_instance = None

def get_proxy(port=None):
    global _proxy_instance
    if _proxy_instance is None:
        _proxy_instance = HLSProxy(host='127.0.0.1', port=(int(port) if port else 0))
        _proxy_instance.start()
        return _proxy_instance

    if port and int(port) != _proxy_instance.port:
        try:
            _proxy_instance.stop()
        except Exception:
            pass
        logger.info("Restarting HLS proxy on new port %s" % port)
        _proxy_instance = HLSProxy(host='127.0.0.1', port=int(port))
        _proxy_instance.start()
    return _proxy_instance

def fetch_stream_url(username):
    """Fetch the M3U8 URL for the given username."""
    # Strip .m3u8 from username if present
    if username.endswith('.m3u8'):
        username = username[:-5]  # Strip .m3u8 from username
    # Check cache first
    now = time.time()
    if username in _username_m3u8_cache:
        cached_time, cached_url = _username_m3u8_cache[username]
        if now - cached_time < CACHE_TTL_SECONDS:
            return cached_url
        else:
            del _username_m3u8_cache[username]  # Expired, remove
    
    api_url = API_ENDPOINT_MODEL.format(username)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.stripchat.com/{username}",
        "User-Agent": FORWARD_HEADERS["User-Agent"]
    }
    try:
        resp = _global_session.get(api_url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if not data or not data.get("cam") or not data.get("user"):
            logger.error("Invalid API response or stream offline")
            return None
        user_data = data["user"]["user"]
        if not user_data["isLive"] or user_data["status"] != "public":
            logger.error(f"Stream offline or private (status: {user_data['status']})")
            return None
        stream_name = data["cam"]["streamName"]
        # Choose URL template based on --best flag
        url_template = M3U8_BESTONLY_URL if _use_best else M3U8_BASE_URL
        m3u8_url = url_template.format(stream_name, stream_name)
        # Cache the result
        _username_m3u8_cache[username] = (now, m3u8_url)
        return m3u8_url
    except Exception as e:
        logger.error(f"Failed to fetch stream URL for {username}: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Standalone HLS Proxy for Stripchat")
    parser.add_argument('username', nargs='?', help='Username of the streamer (optional; if not provided, proxy waits for /username requests)')
    parser.add_argument('--port', type=int, default=0, help='Port to run the proxy on (default: auto)')
    parser.add_argument('--host', type=str, default='127.0.0.1', help='Host to bind to (default: 127.0.0.1)')
    parser.add_argument('--best', action='store_true', help='Use best quality only playlist instead of variants playlist')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging (includes debug messages from ProxyHandler)')
    args = parser.parse_args()

    # Adjust logging level based on verbose flag
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.INFO)

    # Set global flag for best quality
    global _use_best
    _use_best = args.best

    global _stream_m3u8_url
    if args.username:
        # Fetch the M3U8 URL for the username and set as default
        m3u8_url = fetch_stream_url(args.username)
        if not m3u8_url:
            logger.error("Could not fetch stream URL. Exiting.")
            return
        _stream_m3u8_url = m3u8_url
        logger.info(f"Default stream set for username: {args.username}")

    # Load decode key once at startup
    global _decode_key
    script_dir = os.path.dirname(os.path.abspath(__file__))
    key_file = os.path.join(script_dir, 'key.txt')
    try:
        with open(key_file, 'r') as f:
            _decode_key = f.read().strip()
        if not _decode_key:
            logger.error("Decode key is empty in key.txt. Playback will fail for encrypted streams.")
            _decode_key = None
        else:
            logger.debug("Loaded decode key from key.txt")
    except FileNotFoundError:
        logger.error("key.txt not found in the script directory. Please create it with the decode key.")
        _decode_key = None
    except Exception as e:
        logger.error(f"Failed to read decode key from key.txt: {e}")
        _decode_key = None

    # Start the proxy
    proxy = HLSProxy(host=args.host, port=args.port)
    host, port = proxy.start()

    # Log the URL to use
    if _stream_m3u8_url:
        proxy_url = f"http://{host}:{port}/?url={urllib.parse.quote(_stream_m3u8_url)}"
        logger.info(f"Default stream proxy URL: {proxy_url}")
        logger.info(f"Shortform default URL: http://{host}:{port}/")
    logger.info(f"Proxy listening on http://{host}:{port}/ (supports /username for dynamic streams)")
    logger.info("Proxy is running. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping proxy...")
        proxy.stop()

if __name__ == "__main__":
    main()