import base64
import logging
import pkgutil
import time
from collections import OrderedDict
from functools import lru_cache
from socket import AF_INET, AF_INET6

import requests
import requests.packages.urllib3.util.connection as urllib3_connection
from requests.packages.urllib3.util.connection import allowed_gai_family

from streamlink import __version__, plugins
from streamlink.cache import Cache, MemoryCache
from streamlink.compat import is_win32
from streamlink.exceptions import NoPluginError, PluginError
from streamlink.logger import StreamlinkLogger
from streamlink.options import Options
from streamlink.plugin import Plugin, api
from streamlink.utils import load_module, update_scheme
from streamlink.utils.l10n import Localization

# Ensure that the Logger class returned is Streamslink's for using the API (for backwards compatibility)
logging.setLoggerClass(StreamlinkLogger)
log = logging.getLogger(__name__)


class PythonDeprecatedWarning(UserWarning):
    pass


class Streamlink:
    """A Streamlink session is used to keep track of plugins,
       options and log settings."""

    def __init__(self, options=None):
        self.http = api.HTTPSession()
        self.options = Options({
            "interface": None,
            "ipv4": False,
            "ipv6": False,
            "dash-live-edge": 3,
            "hds-live-edge": 10.0,
            "hds-segment-attempts": 3,
            "hds-segment-threads": 1,
            "hds-segment-timeout": 10.0,
            "hds-timeout": 60.0,
            "hls-live-edge": 3,
            # LJQ 设置 ts-url-add-m3u-url-params 默认值
            "ts-url-add-m3u-url-params": False,
            "hls-segment-host": None,
            "hls-segment-attempts": 3,
            "hls-segment-ignore-names": [],
            "hls-segment-threads": 1,
            "hls-segment-timeout": 10.0,
            "hls-segment-stream-data": False,
            "hls-timeout": 60.0,
            "hls-playlist-reload-attempts": 3,
            "hls-playlist-reload-time": "default",
            "hls-start-offset": 0,
            "hls-duration": None,
            "http-stream-timeout": 60.0,
            "hls-token-period": 60.0,
            "ringbuffer-size": 1024 * 1024 * 16,  # 16 MB
            "rtmp-timeout": 60.0,
            "rtmp-rtmpdump": is_win32 and "rtmpdump.exe" or "rtmpdump",
            "rtmp-proxy": None,
            "stream-segment-attempts": 3,
            "stream-segment-threads": 1,
            "stream-segment-timeout": 10.0,
            "stream-timeout": 60.0,
            "drm-decrypt-key": None,
            "drm-temp-dir": None,
            "stream-max-playback-duration": 0,
            "subprocess-errorlog": False,
            "subprocess-errorlog-path": None,
            "ffmpeg-ffmpeg": None,
            "ffmpeg-fout": None,
            "ffmpeg-video-transcode": None,
            "ffmpeg-audio-transcode": None,
            "ffmpeg-copyts": False,
            "ffmpeg-start-at-zero": False,
            "mux-subtitles": False,
            "locale": None,
            "user-input-requester": None
        })
        # felix add update headers cookies
        self.cache = Cache(filename="StreamlinkSession.json")
        self.memory_cache = MemoryCache()

        if options:
            self.options.update(options)
        self.plugins = OrderedDict({})
        self.load_builtin_plugins()
        self.start_playback_time = time.time()

    def set_option(self, key, value):
        """Sets general options used by plugins and streams originating
        from this session object.

        :param key: key of the option
        :param value: value to set the option to


        **Available options**:

        ======================== =========================================
        interface                (str) Set the network interface,
                                 default: ``None``
        ipv4                     (bool) Resolve address names to IPv4 only.
                                 This option overrides ipv6, default: ``False``
        ipv6                     (bool) Resolve address names to IPv6 only.
                                 This option overrides ipv4, default: ``False``

        dash-live-edge           (int) How many segments from the end
                                 to start live streams on, default: ``3``

        hds-live-edge            (float) Specify the time live HDS
                                 streams will start from the edge of
                                 stream, default: ``10.0``

        hds-segment-attempts     (int) How many attempts should be done
                                 to download each HDS segment, default: ``3``

        hds-segment-threads      (int) The size of the thread pool used
                                 to download segments, default: ``1``

        hds-segment-timeout      (float) HDS segment connect and read
                                 timeout, default: ``10.0``

        hds-timeout              (float) Timeout for reading data from
                                 HDS streams, default: ``60.0``

        ts-url-add-m3u-url-params   (bool)Whether to splice m3u8 URL parameters after TS URL, 默认: ``False``

        hls-live-edge            (int) How many segments from the end
                                 to start live streams on, default: ``3``

        hls-segment-host         (str) Specify the hostname for each link, default: ``None``

        hls-segment-attempts     (int) How many attempts should be done
                                 to download each HLS segment, default: ``3``

        hls-segment-ignore-names (str[]) List of segment names without
                                 file endings which should get filtered out,
                                 default: ``[]``

        hls-segment-threads      (int) The size of the thread pool used
                                 to download segments, default: ``1``

        hls-segment-stream-data  (bool) Stream HLS segment downloads,
                                 default: ``False``

        hls-segment-timeout      (float) HLS segment connect and read
                                 timeout, default: ``10.0``

        hls-timeout              (float) Timeout for reading data from
                                 HLS streams, default: ``60.0``

        hls-token-period        Time for update token period
                                Default is ``60.0``

        http-proxy               (str) Specify a HTTP proxy to use for
                                 all HTTP requests

        https-proxy              (str) Specify a HTTPS proxy to use for
                                 all HTTPS requests

        http-cookies             (dict or str) A dict or a semi-colon (;)
                                 delimited str of cookies to add to each
                                 HTTP request, e.g. ``foo=bar;baz=qux``

        http-headers             (dict or str) A dict or semi-colon (;)
                                 delimited str of headers to add to each
                                 HTTP request, e.g. ``foo=bar;baz=qux``

        http-query-params        (dict or str) A dict or a ampersand (&)
                                 delimited string of query parameters to
                                 add to each HTTP request,
                                 e.g. ``foo=bar&baz=qux``

        http-trust-env           (bool) Trust HTTP settings set in the
                                 environment, such as environment
                                 variables (HTTP_PROXY, etc) and
                                 ~/.netrc authentication

        http-ssl-verify          (bool) Verify SSL certificates,
                                 default: ``True``

        http-ssl-cert            (str or tuple) SSL certificate to use,
                                 can be either a .pem file (str) or a
                                 .crt/.key pair (tuple)

        http-timeout             (float) General timeout used by all HTTP
                                 requests except the ones covered by
                                 other options, default: ``20.0``

        http-stream-timeout      (float) Timeout for reading data from
                                 HTTP streams, default: ``60.0``

        subprocess-errorlog      (bool) Log errors from subprocesses to
                                 a file located in the temp directory

        subprocess-errorlog-path (str) Log errors from subprocesses to
                                 a specific file

        ringbuffer-size          (int) The size of the internal ring
                                 buffer used by most stream types,
                                 default: ``16777216`` (16MB)

        rtmp-proxy               (str) Specify a proxy (SOCKS) that RTMP
                                 streams will use

        rtmp-rtmpdump            (str) Specify the location of the
                                 rtmpdump executable used by RTMP streams,
                                 e.g. ``/usr/local/bin/rtmpdump``

        rtmp-timeout             (float) Timeout for reading data from
                                 RTMP streams, default: ``60.0``

        ffmpeg-ffmpeg            (str) Specify the location of the
                                 ffmpeg executable use by Muxing streams
                                 e.g. ``/usr/local/bin/ffmpeg``

        ffmpeg-verbose           (bool) Log stderr from ffmpeg to the
                                 console

        ffmpeg-verbose-path      (str) Specify the location of the
                                 ffmpeg stderr log file

        ffmpeg-fout              (str) The output file format
                                 when muxing with ffmpeg
                                 e.g. ``matroska``

        ffmpeg-video-transcode   (str) The codec to use if transcoding
                                 video when muxing with ffmpeg
                                 e.g. ``h264``

        ffmpeg-audio-transcode   (str) The codec to use if transcoding
                                 audio when muxing with ffmpeg
                                 e.g. ``aac``

        ffmpeg-copyts            (bool) When used with ffmpeg, do not shift input timestamps.

        ffmpeg-start-at-zero     (bool) When used with ffmpeg and copyts,
                                 shift input timestamps so they start at zero
                                 default: ``False``

        mux-subtitles            (bool) Mux available subtitles into the
                                 output stream.

        http-report-uri  (str) URI will report when stream is abnormal

        http-report-interval  (int) The report URI's interval

        stream-segment-attempts  (int) How many attempts should be done
                                 to download each segment, default: ``3``.
                                 General option used by streams not
                                 covered by other options.

        stream-segment-threads   (int) The size of the thread pool used
                                 to download segments, default: ``1``.
                                 General option used by streams not
                                 covered by other options.

        stream-segment-timeout   (float) Segment connect and read
                                 timeout, default: ``10.0``.
                                 General option used by streams not
                                 covered by other options.

        stream-timeout           (float) Timeout for reading data from
                                 stream, default: ``60.0``.
                                 General option used by streams not
                                 covered by other options.

        drm-decrypt-key          (str) The decrypt key for decrypting DRM encrypted stream.

        drm-temp-dir             (str) The dir for saving all temporary DRM data, including encryption and decryption data.

        stream-max-playback-duration    (float) Duration of playing stream, default: ``0``.
                                 Zero is unlimited playback time.
                                 Otherwise, it will stop playing stream if playback time is greater than the max duration.

        locale                   (str) Locale setting, in the RFC 1766 format
                                 eg. en_US or es_ES
                                 default: ``system locale``.

        user-input-requester     (UserInputRequester) instance of UserInputRequester
                                 to collect input from the user at runtime. Must be
                                 set before the plugins are loaded.
                                 default: ``UserInputRequester``.
        ======================== =========================================

        """

        if key == "interface":
            for scheme, adapter in self.http.adapters.items():
                if scheme not in ("http://", "https://"):
                    continue
                if not value:
                    adapter.poolmanager.connection_pool_kw.pop("source_address")
                else:
                    adapter.poolmanager.connection_pool_kw.update(
                        # https://docs.python.org/3/library/socket.html#socket.create_connection
                        source_address=(value, 0)
                    )
            self.options.set(key, None if not value else value)

        elif key == "ipv4" or key == "ipv6":
            self.options.set(key, value)
            if value:
                self.options.set("ipv6" if key == "ipv4" else "ipv4", False)
                urllib3_connection.allowed_gai_family = \
                    (lambda: AF_INET) if key == "ipv4" else (lambda: AF_INET6)
            else:
                urllib3_connection.allowed_gai_family = allowed_gai_family

        elif key == "http-proxy":
            self.http.proxies["http"] = update_scheme("http://", value)
            if "https" not in self.http.proxies:
                self.http.proxies["https"] = update_scheme("http://", value)

        elif key == "https-proxy":
            self.http.proxies["https"] = update_scheme("https://", value)
        elif key == "http-cookies":
            if isinstance(value, dict):
                self.http.cookies.update(value)
            else:
                self.http.parse_cookies(value)
        elif key == "http-headers":
            if isinstance(value, dict):
                self.http.headers.update(value)
            else:
                self.http.parse_headers(value)
        elif key == "http-query-params":
            if isinstance(value, dict):
                self.http.params.update(value)
            else:
                self.http.parse_query_params(value)
        elif key == "http-trust-env":
            self.http.trust_env = value
        elif key == "http-ssl-verify":
            self.http.verify = value
        elif key == "http-disable-dh":
            if value:
                requests.packages.urllib3.util.ssl_.DEFAULT_CIPHERS += ':!DH'
                try:
                    requests.packages.urllib3.contrib.pyopenssl.DEFAULT_SSL_CIPHER_LIST = \
                        requests.packages.urllib3.util.ssl_.DEFAULT_CIPHERS.encode("ascii")
                except AttributeError:
                    # no ssl to disable the cipher on
                    pass
        elif key == "http-ssl-cert":
            self.http.cert = value
        elif key == "http-timeout":
            self.http.timeout = value
        # LJQ: http-report-uri
        elif key == "http-report-uri":
            if isinstance(value, str) and not value.startswith('http'):
                value = base64.urlsafe_b64decode(value).decode('utf-8')
            self.http.report_uri = value
        # LJQ: http-report-interval
        elif key == "http-report-interval":
            self.http.report_interval = value
        # LJQ: stop-stream-playing
        elif key == "stop-stream-playing":
            self.http.stop_stream_playing = value
        # LJQ: error-http-status-codes
        elif key == "error-http-status-codes":
            self.http.error_http_status_codes = value
        else:
            self.options.set(key, value)

    def get_option(self, key):
        """Returns current value of specified option.

        :param key: key of the option

        """

        if key == "http-proxy":
            return self.http.proxies.get("http")
        elif key == "https-proxy":
            return self.http.proxies.get("https")
        elif key == "http-cookies":
            return self.http.cookies
        elif key == "http-headers":
            return self.http.headers
        elif key == "http-query-params":
            return self.http.params
        elif key == "http-trust-env":
            return self.http.trust_env
        elif key == "http-ssl-verify":
            return self.http.verify
        elif key == "http-ssl-cert":
            return self.http.cert
        elif key == "http-timeout":
            return self.http.timeout
        # LJQ: http-report-uri
        elif key == "http-report-uri":
            return self.http.report_uri
        # LJQ: http-report-interval
        elif key == "http-report-interval":
            return self.http.report_interval
        # LJQ: stop-stream-playing
        elif key == "stop-stream-playing":
            return self.http.stop_stream_playing
        # LJQ: error-http-status-codes
        elif key == "error-http-status-codes":
            return self.http.error_http_status_codes
        else:
            return self.options.get(key)

    def set_plugin_option(self, plugin, key, value):
        """Sets plugin specific options used by plugins originating
        from this session object.

        :param plugin: name of the plugin
        :param key: key of the option
        :param value: value to set the option to

        """

        if plugin in self.plugins:
            plugin = self.plugins[plugin]
            plugin.set_option(key, value)

    def get_plugin_option(self, plugin, key):
        """Returns current value of plugin specific option.

        :param plugin: name of the plugin
        :param key: key of the option

        """

        if plugin in self.plugins:
            plugin = self.plugins[plugin]
            return plugin.get_option(key)

    @lru_cache(maxsize=128)
    def resolve_url(self, url, follow_redirect=True):
        """Attempts to find a plugin that can use this URL.

        The default protocol (http) will be prefixed to the URL if
        not specified.

        Raises :exc:`NoPluginError` on failure.

        :param url: a URL to match against loaded plugins
        :param follow_redirect: follow redirects

        """
        url = update_scheme("http://", url)

        available_plugins = []
        for name, plugin in self.plugins.items():
            if plugin.can_handle_url(url):
                available_plugins.append(plugin)

        available_plugins.sort(key=lambda x: x.priority(url), reverse=True)
        if available_plugins:
            return available_plugins[0](url)

        if follow_redirect:
            # Attempt to handle a redirect URL
            try:
                res = self.http.head(url, allow_redirects=True, acceptable_status=[501])

                # Fall back to GET request if server doesn't handle HEAD.
                if res.status_code == 501:
                    res = self.http.get(url, stream=True)

                if res.url != url:
                    return self.resolve_url(res.url, follow_redirect=follow_redirect)
            except PluginError:
                pass

        raise NoPluginError

    def resolve_url_no_redirect(self, url):
        """Attempts to find a plugin that can use this URL.

        The default protocol (http) will be prefixed to the URL if
        not specified.

        Raises :exc:`NoPluginError` on failure.

        :param url: a URL to match against loaded plugins

        """
        return self.resolve_url(url, follow_redirect=False)

    def streams(self, url, **params):
        """Attempts to find a plugin and extract streams from the *url*.

        *params* are passed to :func:`Plugin.streams`.

        Raises :exc:`NoPluginError` if no plugin is found.
        """

        plugin = self.resolve_url(url)
        return plugin.streams(**params)

    def get_plugins(self):
        """Returns the loaded plugins for the session."""

        return self.plugins

    def load_builtin_plugins(self):
        self.load_plugins(plugins.__path__[0])

    def load_plugins(self, path: str) -> bool:
        """Attempt to load plugins from the path specified.

        :param path: full path to a directory where to look for plugins
        :return: success
        """
        success = False
        user_input_requester = self.get_option("user-input-requester")
        for loader, name, ispkg in pkgutil.iter_modules([path]):
            # set the full plugin module name
            module_name = f"streamlink.plugins.{name}"
            try:
                mod = load_module(module_name, path)
            except ImportError:
                log.exception(f"Failed to load plugin {name} from {path}\n")
                continue

            if not hasattr(mod, "__plugin__") or not issubclass(mod.__plugin__, Plugin):
                continue
            success = True
            plugin = mod.__plugin__
            plugin.bind(self, name, user_input_requester)
            if plugin.module in self.plugins:
                log.debug(f"Plugin {plugin.module} is being overridden by {mod.__file__}")
            self.plugins[plugin.module] = plugin

        return success

    @property
    def version(self):
        return __version__

    @property
    def localization(self):
        return Localization(self.get_option("locale"))


__all__ = ["Streamlink"]
