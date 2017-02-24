# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details
# http://www.gnu.org/licenses/gpl-3.0.txt

import urwid

from ..table import Table
from .plist_columns import TUICOLUMNS


class PeerListItemWidget(urwid.WidgetWrap):
    def __init__(self, peer, cells):
        self._peer = peer
        self._cells = cells
        self.update(peer)
        super().__init__(urwid.AttrMap(cells, 'peerlist'))

    def update(self, peer):
        for widget in self._cells.original_widget.widgets:
            widget.update(peer)
        self._peer = peer

    @property
    def pid(self):
        return self._peer['id']

    @property
    def peer(self):
        return self._peer


class PeerListWidget(urwid.WidgetWrap):
    def __init__(self, srvapi, tfilter, pfilter, columns, sort=None, title=None):
        self._sort = sort

        # Create peer filter generator
        if pfilter is not None:
            def filter_peers(peers):
                yield from pfilter.apply(peers)
        else:
            def filter_peers(peers):
                yield from peers
        self._maybe_filter_peers = filter_peers

        # Create the fixed part of the title (everything minus the number of peers listed)
        # If title is not given, create one from filter and sort order
        if title is None:
            # tfilter is either None or an actual TorrentFilter instance
            title = str(tfilter or 'all')
        if pfilter:
            title += ' %s' % pfilter
        if sort is not None:
            sortstr = str(sort)
            if sortstr is not self._sort.DEFAULT_SORT:
                title += ' {%s}' % sortstr
        self._title_base = title
        self.title_updater = None

        self._peers = ()
        self._initialized = False

        self._table = Table(**TUICOLUMNS)
        self._table.columns = columns

        self._listbox = urwid.ListBox(urwid.SimpleListWalker([]))
        pile = urwid.Pile([
            ('pack', urwid.AttrMap(self._table.headers, 'peerlist.header')),
            self._listbox
        ])
        super().__init__(urwid.AttrMap(pile, 'peerlist'))

        self._poller = srvapi.create_poller(
            srvapi.torrent.torrents, tfilter, keys=('peers', 'name', 'id'))
        self._poller.on_response(self._handle_response)

    def _handle_response(self, response):
        if response is None or not response.torrents:
            self.clear()
        else:
            def peers_combined(torrents):
                for t in torrents:
                    yield from self._maybe_filter_peers(t['peers'])
            self._peers = {p['id']:p for p in peers_combined(response.torrents)}

        if self.title_updater is not None:
            self.title_updater(self.title)

        self._invalidate()

    def render(self, size, focus=False):
        if self._peers is not None:
            self._update_listitems()
            self._peers = None
        return super().render(size, focus)

    def _update_listitems(self):
        pdict = self._peers
        walker = self._listbox.body
        dead_pws = []
        for pw in walker:  # pw = PeerListItemWidget
            pid = pw.pid
            try:
                # Update existing peer widget with new data
                pw.update(pdict[pid])
                del pdict[pid]
            except KeyError:
                # Peer no longer exists
                dead_pws.append(pw)

        # Remove list items
        for pw in dead_pws:
            walker.remove(pw)

        # Any peers that haven't been used to update an existing peer widget are new
        for pid in pdict:
            self._table.register(pid)
            row = urwid.AttrMap(self._table.get_row(pid), attr_map='peerlist')
            walker.append(PeerListItemWidget(pdict[pid], row))

        # Sort peers
        if self._sort is not None:
            self._sort.apply(walker,
                            item_getter=lambda pw: pw.peer,
                            inplace=True)

    def clear(self):
        """Remove all list items"""
        self._table.clear()
        self._listbox.body[:] = []
        self._listbox._invalidate()

    @property
    def title(self):
        title = [self._title_base]

        # If this method was called before rendering, the contents of the
        # listbox widget are inaccurate and we have to use self._peers.  But
        # if we're called after rendering, self._peers is reset to None.
        if self._peers is not None:
            title.append('[%d]' % len(self._peers))
        else:
            title.append('[%d]' % len(self._listbox.body))

        return ' '.join(title)
