# Copyright (C) 2019-2021 Unrud <unrud@outlook.com>
#
# This file is part of Video Downloader.
#
# Video Downloader is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Video Downloader is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Video Downloader.  If not, see <http://www.gnu.org/licenses/>.

import gettext
import os
import subprocess
import traceback
import typing

from gi.repository import Gio, GLib, GObject

from video_downloader import downloader
from video_downloader.downloader import MAX_RESOLUTION
from video_downloader.util import (bind_property, expand_path, g_log,
                                   languages_from_locale)

N_ = gettext.gettext


class Model(GObject.GObject, downloader.Handler):
    __gsignals__ = {
        'download-pulse': (GObject.SIGNAL_RUN_FIRST, None, ())
    }
    state = GObject.Property(type=str, default='start')
    mode = GObject.Property(type=str, default='audio')
    url = GObject.Property(type=str)
    error = GObject.Property(type=str)
    resolution = GObject.Property(type=GObject.TYPE_UINT, default=1080)
    download_folder = GObject.Property(type=str)
    # absolute path to dir of active/finished download (empty if no download)
    finished_download_dir = GObject.Property(type=str)
    # TYPE_STRV is None by default, empty list can be None or []
    finished_download_filenames = GObject.Property(type=GObject.TYPE_STRV)
    automatic_subtitles = GObject.Property(type=GObject.TYPE_STRV)
    prefer_mpeg = GObject.Property(type=bool, default=False)
    download_playlist_index = GObject.Property(type=GObject.TYPE_INT64)
    download_playlist_count = GObject.Property(type=GObject.TYPE_INT64)
    download_filename = GObject.Property(type=str)
    download_title = GObject.Property(type=str)
    download_thumbnail = GObject.Property(type=str)
    # 0.0 - 1.0 (inclusive), negative if unknown:
    download_progress = GObject.Property(type=float, default=-1)
    download_bytes = GObject.Property(type=GObject.TYPE_INT64, default=-1)
    download_bytes_total = GObject.Property(type=GObject.TYPE_INT64,
                                            default=-1)
    download_speed = GObject.Property(type=GObject.TYPE_INT64, default=-1)
    download_eta = GObject.Property(type=GObject.TYPE_INT64, default=-1)
    resolutions = [
        (MAX_RESOLUTION, N_('Best')),
        (4320, N_('4320p (8K)')),
        (2160, N_('2160p (4K)')),
        (1440, N_('1440p (HD)')),
        (1080, N_('1080p (HD)')),
        (720, N_('720p (HD)')),
        (480, N_('480p')),
        (360, N_('360p')),
        (240, N_('240p')),
        (144, N_('144p'))]

    _global_download_lock = set()

    def __init__(self, handler=None):
        super().__init__()
        self._handler = handler
        self._downloader = downloader.Downloader(self)
        self._active_download_lock = None
        self._filemanager_proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES |
            Gio.DBusProxyFlags.DO_NOT_CONNECT_SIGNALS |
            Gio.DBusProxyFlags.DO_NOT_AUTO_START_AT_CONSTRUCTION, None,
            'org.freedesktop.FileManager1', '/org/freedesktop/FileManager1',
            'org.freedesktop.FileManager1')
        self.actions = Gio.SimpleActionGroup.new()
        self.actions.add_action_entries([
            ('download', lambda *_: self.set_property('state', 'download')),
            ('cancel', lambda *_: self.set_property('state', 'cancel')),
            ('back', lambda *_: self.set_property('state', 'start')),
            ('open-finished-download-dir',
             lambda *_: self._open_finished_download_dir())])
        bind_property(self, 'url', self.actions.lookup_action('download'),
                      'enabled', bool)
        self._prev_state = None
        bind_property(self, 'state', func_a_to_b=self._state_transition)
        bind_property(self, 'state', self.actions.lookup_action('cancel'),
                      'enabled', lambda s: s == 'download')
        bind_property(self, 'state',
                      self.actions.lookup_action('open-finished-download-dir'),
                      'enabled', lambda s: s == 'success')

    def _state_transition(self, state):
        if state == 'start':
            assert self._prev_state != 'download'
            self.error = ''
            self.download_playlist_index = 0
            self.download_playlist_count = 0
            self.download_filename = ''
            self.download_title = ''
            self.download_thumbnail = ''
            self.download_progress = -1
            self.download_bytes = -1
            self.download_bytes_total = -1
            self.download_speed = -1
            self.download_eta = -1
            self.finished_download_filenames = []
            self.finished_download_dir = ''
        elif state == 'download':
            assert self._prev_state == 'start'
            self.finished_download_dir = expand_path(self.download_folder)
            self._downloader.start()
        elif state == 'cancel':
            assert self._prev_state == 'download'
            self._downloader.cancel()
        elif state in ['success', 'error']:
            assert self._prev_state == 'download'
        else:
            assert False, 'invalid value for \'state\' property: %r' % state
        self._prev_state = state

    def _open_finished_download_dir(self):
        assert self.finished_download_dir
        if len(self.finished_download_filenames or []) == 1:
            method = 'ShowItems'
            paths = [os.path.join(self.finished_download_dir, filename) for
                     filename in (self.finished_download_filenames or [])]
        else:
            method = 'ShowFolders'
            paths = [self.finished_download_dir]
        parameters = GLib.Variant(
            '(ass)', ([Gio.File.new_for_path(p).get_uri() for p in paths], ''))
        try:
            self._filemanager_proxy.call_sync(
                method, parameters, Gio.DBusCallFlags.NONE, -1)
        except GLib.Error:
            g_log(None, GLib.LogLevelFlags.LEVEL_WARNING, '%s',
                  traceback.format_exc())
            subprocess.run(['xdg-open', self.finished_download_dir],
                           check=True)

    def shutdown(self):
        self._downloader.shutdown()

    def on_pulse(self):
        assert self.state in ['download', 'cancel']
        self.emit('download-pulse')

    def on_finished(self, success):
        assert self.state in ['download', 'cancel']
        self._download_unlock()
        if self.state == 'cancel':
            self.state = 'start'
        else:
            self.state = 'success' if success else 'error'

    def get_download_dir(self):
        assert self.state in ['download', 'cancel']
        return self.finished_download_dir

    def get_prefer_mpeg(self):
        assert self.state in ['download', 'cancel']
        return self.prefer_mpeg

    def get_automatic_subtitles(self):
        assert self.state in ['download', 'cancel']
        return [*languages_from_locale(), *(self.automatic_subtitles or [])]

    def get_url(self):
        assert self.state in ['download', 'cancel']
        return self.url

    def get_mode(self):
        assert self.state in ['download', 'cancel']
        return self.mode

    def get_resolution(self):
        assert self.state in ['download', 'cancel']
        return self.resolution

    def on_playlist_request(self):
        assert self.state in ['download', 'cancel']
        return self._handler.on_playlist_request()

    def on_login_request(self):
        assert self.state in ['download', 'cancel']
        return self._handler.on_login_request()

    def on_videopassword_request(self):
        assert self.state in ['download', 'cancel']
        return self._handler.on_videopassword_request()

    def on_error(self, msg):
        assert self.state in ['download', 'cancel']
        self.error = msg

    def on_progress(self, filename, progress, bytes_, bytes_total, eta, speed):
        assert self.state in ['download', 'cancel']
        self.download_filename = filename
        self.download_progress = progress
        self.download_bytes = bytes_
        self.download_bytes_total = bytes_total
        self.download_eta = eta
        self.download_speed = speed

    def on_download_start(self, playlist_index, playlist_count, title):
        assert self.state in ['download', 'cancel']
        self.download_playlist_index = playlist_index
        self.download_playlist_count = playlist_count
        self.download_title = title

    def _download_unlock(self):
        if self._active_download_lock:
            self._global_download_lock.remove(self._active_download_lock)
            self._active_download_lock = None

    def on_download_lock(self, name):
        assert self.state in ['download', 'cancel']
        assert self._active_download_lock is None
        if name in self._global_download_lock:
            return False
        self._global_download_lock.add(name)
        self._active_download_lock = name
        return True

    def on_download_thumbnail(self, thumbnail):
        assert self.state in ['download', 'cancel']
        self.download_thumbnail = thumbnail

    def on_download_finished(self, filename):
        assert self.state in ['download', 'cancel']
        self._download_unlock()
        self.finished_download_filenames = [
            *(self.finished_download_filenames or []), filename]


class Handler:
    def on_playlist_request(self) -> bool:
        raise NotImplementedError

    #                                          username password
    def on_login_request(self) -> typing.Tuple[str,     str]:
        raise NotImplementedError

    #                                     password
    def on_videopassword_request(self) -> str:
        raise NotImplementedError
