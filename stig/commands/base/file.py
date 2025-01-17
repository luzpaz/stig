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

import asyncio

from ... import objects
from ...completion import candidates
from .. import CmdError, CommandMeta
from . import _mixin as mixin
from ._common import make_COLUMNS_doc, make_SCRIPTING_doc, make_X_FILTER_spec

from ...logging import make_logger  # isort:skip
log = make_logger(__name__)


class ListFilesCmdbase(mixin.get_file_columns, metaclass=CommandMeta):
    name = 'filelist'
    aliases = ('fls', 'lsf')
    provides = set()
    category = 'file'
    description = 'List files of torrent(s)'
    usage = ('filelist [<OPTIONS>]',
             'filelist [<OPTIONS>] <TORRENT FILTER>',
             'filelist [<OPTIONS>] <TORRENT FILTER> <FILE FILTER>')
    examples = ('filelist',
                'filelist size<100MB',
                'filelist A.Torrent.with.Files priority=low')
    argspecs = (
        make_X_FILTER_spec('TORRENT', or_focused=True, nargs='?'),
        make_X_FILTER_spec('FILE', or_focused=False, nargs='?'),
        {'names': ('--columns', '-c'),
         'default_description': "current value of 'columns.files' setting",
         'description': ('Comma-separated list of column names '
                         "(see COLUMNS section)")},
    )

    from ...views.file import COLUMNS
    more_sections = {
        'COLUMNS': make_COLUMNS_doc(COLUMNS, '--columns', 'columns.files'),
        'SCRIPTING': make_SCRIPTING_doc(name),
    }

    async def run(self, TORRENT_FILTER, FILE_FILTER, columns):
        columns = objects.localcfg['columns.files'] if columns is None else columns
        try:
            columns = self.get_file_columns(columns)
            tfilter = self.select_torrents(TORRENT_FILTER,
                                           allow_no_filter=False,
                                           discover_torrent=True)
            ffilter = self.select_files(FILE_FILTER,
                                        allow_no_filter=True,
                                        discover_file=False)
        except ValueError as e:
            raise CmdError(e)

        log.debug('Listing %s files of %s torrents', ffilter, tfilter)

        if asyncio.iscoroutinefunction(self.make_file_list):
            await self.make_file_list(tfilter, ffilter, columns)
        else:
            self.make_file_list(tfilter, ffilter, columns)

    @classmethod
    def completion_candidates_posargs(cls, args):
        """Complete positional arguments"""
        posargs = args.posargs({('--columns', '-c'): 1})
        if posargs.curarg_index == 1:
            return candidates.torrent_filter(args.curarg)
        elif posargs.curarg_index == 2:
            torrent_filter = posargs[1]
            return candidates.file_filter(args.curarg, torrent_filter)

    @classmethod
    def completion_candidates_params(cls, option, args):
        """Complete parameters (e.g. --option parameter1,parameter2)"""
        if option == '--columns':
            return candidates.column_names('files')


class PriorityCmdbase(metaclass=CommandMeta):
    name = 'priority'
    aliases = ('prio',)
    provides = set()
    category = 'file'
    description = 'Change download priority of files'
    usage = ('priority <PRIORITY>',
             'priority <PRIORITY> <TORRENT FILTER>',
             'priority <PRIORITY> <TORRENT FILTER> <FILE FILTER>')
    examples = ('priority low',
                'priority high "that torrent" size>12M')
    _PRIORITY = {'off'    : ('o', '0', 'off'),
                 'low'    : ('l', '-', 'low'),
                 'normal' : ('n', '=', 'normal'),
                 'high'   : ('h', '+', 'high')}
    argspecs = (
        {'names': ('PRIORITY',),
         'description': 'File priority; must be %s, %s or %s' % (
             '/'.join(p[2] for p in _PRIORITY.values()),
             '/'.join(p[0] for p in _PRIORITY.values()),
             '/'.join(p[1] for p in _PRIORITY.values()))},
        make_X_FILTER_spec('TORRENT', or_focused=True, nargs='?'),
        make_X_FILTER_spec('FILE', or_focused=True, nargs='?'),
    )

    async def run(self, PRIORITY, TORRENT_FILTER, FILE_FILTER):
        priority = None
        for p,names in self._PRIORITY.items():
            if PRIORITY in names:
                priority = p
                break
        if priority is None:
            raise CmdError('Invalid priority: %r' % PRIORITY)

        # Whether the user manually typed a filter
        utilize_tui = not bool(TORRENT_FILTER)

        try:
            tfilter = self.select_torrents(TORRENT_FILTER,
                                           allow_no_filter=False,
                                           discover_torrent=True)

            # If the user specified a filter instead of selecting via the TUI,
            # ignore focused/marked files.
            log.debug('%sdiscovering file(s)', '' if utilize_tui else 'Not ')
            ffilter = self.select_files(FILE_FILTER,
                                        allow_no_filter=True,
                                        discover_file=utilize_tui)
        except ValueError as e:
            raise CmdError(e)

        if not utilize_tui:
            self.info('New download priority of %s files in %s torrents: %s' %
                      ('all' if ffilter is None else ffilter, tfilter, priority))
            quiet = False
        else:
            # We're operating on focused or marked files and changes are
            # indiciated by the updated file list, so no info messages
            # necessary.
            quiet = True

        log.debug('Setting file download priority to %s for %s files of %s torrents',
                  priority, ffilter, tfilter)
        response = await self.make_request(
            objects.srvapi.torrent.file_priority(tfilter, ffilter, priority),
            polling_frenzy=True, quiet=quiet)
        if not response.success:
            raise CmdError()

    @classmethod
    def completion_candidates_posargs(cls, args):
        """Complete positional arguments"""
        if args.curarg_index == 1:
            # "off", "low", "normal" or "high"
            return candidates.Candidates((p[2] for p in cls._PRIORITY.values()),
                                         label='Priority')
        elif args.curarg_index == 2:
            return candidates.torrent_filter(args.curarg)
        elif args.curarg_index == 3:
            torrent_filter = args[2]
            return candidates.file_filter(args.curarg, torrent_filter)
