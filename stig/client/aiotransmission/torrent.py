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

from ...logging import make_logger
log = make_logger(__name__)

from .. import tkeys as tkeys
from .. import utils
from .. import base

from os.path import normpath


# Some values need to be modified to comply with our internal standards

def _modify_ratio(raw_torrent):
    #define TR_RATIO_NA  -1
    #define TR_RATIO_INF -2
    ratio = raw_torrent['uploadRatio']
    if ratio == -1:
        return tkeys.Ratio.NOT_APPLICABLE
    elif ratio == -2:
        return tkeys.Ratio.UNKNOWN
    else:
        return ratio


def _modify_eta(raw_torrent):
    #define TR_ETA_NOT_AVAIL -1
    #define TR_ETA_UNKNOWN -2
    seconds = raw_torrent['eta']
    if seconds == -1:
        return tkeys.Timedelta.NOT_APPLICABLE
    elif seconds == -2:
        return tkeys.Timedelta.UNKNOWN
    else:
        return seconds


def _count_seeds(raw_torrent):
    trackerStats = raw_torrent['trackerStats']
    if trackerStats:
        return max(t['seederCount'] for t in trackerStats)
    else:
        return tkeys.SeedCount.UNKNOWN


def _is_isolated(raw_torrent):
    """Return whether this torrent can find any peers via trackers or DHT"""
    if not raw_torrent['isPrivate']:
        return False  # DHT is used

    # Torrent has trackers?
    trackerStats = raw_torrent['trackerStats']
    if trackerStats:
        # Did we try to connect to a tracker?
        if any(tracker['hasAnnounced'] for tracker in trackerStats):
            # Did at least one tracker respond?
            if any(tracker['lastAnnounceSucceeded'] for tracker in trackerStats):
                return False
        # We didn't try yet; assume non-isolation
        else:
            return False
    return True  # No way to find any peers


def _make_status(t):
    Status = tkeys.Status
    statuses = []

    # RPC values for 'status' field:
    # TR_STATUS_STOPPED        = 0, /* Torrent is stopped */
    # TR_STATUS_CHECK_WAIT     = 1, /* Queued to check files */
    # TR_STATUS_CHECK          = 2, /* Checking files */
    # TR_STATUS_DOWNLOAD_WAIT  = 3, /* Queued to download */
    # TR_STATUS_DOWNLOAD       = 4, /* Downloading */
    # TR_STATUS_SEED_WAIT      = 5, /* Queued to seed */
    t_status = t['status']
    if t_status == 0:
        statuses.append(Status.STOPPED)
    elif t_status in (1, 2):
        statuses.append(Status.VERIFY)
    if t_status in (1, 3, 5):
        statuses.append(Status.QUEUED)

    if Status.STOPPED not in statuses:
        if _is_isolated(t):
            statuses.append(Status.ISOLATED)
        if t['metadataPercentComplete'] < 1:
            statuses.append(Status.INIT)

        if Status.QUEUED not in statuses:
            if t['peersConnected'] > 0:
                if t['rateDownload'] > 0:
                    statuses.append(Status.DOWNLOAD)
                if t['rateUpload'] > 0:
                    statuses.append(Status.UPLOAD)
                statuses.append(Status.CONNECTED)

            if t['percentDone'] >= 1:
                statuses.append(Status.SEED)

    if all(x not in statuses for x in (Status.UPLOAD,
                                       Status.DOWNLOAD,
                                       Status.VERIFY)):
        statuses.append(Status.IDLE)

    return statuses


def _create_TorrentFileTree(raw_torrent):
    filelist = raw_torrent['fileStats']
    if not filelist:
        # filelist is empty if torrent was added by hash and metadata isn't
        # downloaded yet.
        filelist = [{'name': raw_torrent['name'], 'priority': 0, 'length': 0,
                     'wanted': True, 'id': 0, 'bytesCompleted': 0}]
    else:
        # Combine 'files' and 'fileStats' fields and add the 'id' field, which
        # is the index in the list provided by Transmission.
        if 'files' in raw_torrent:
            for i,(f1,f2) in enumerate(zip(filelist, raw_torrent['files'])):
                f1['id'] = i
                f1.update(f2)
        else:
            for i,f in enumerate(filelist):
                f['id'] = i

    return TorrentFileTree(raw_torrent['id'], entries=filelist)

import os
class TorrentFileTree(base.TorrentFileTreeBase):
    def __init__(self, torrent_id, entries, path=[]):
        if isinstance(entries, TorrentFileTree):
            self._items = entries._items
            return

        self._path = os.sep.join(path)
        items = {}
        subfolders = {}

        for entry in entries:
            parts = entry['name'].split(os.sep, 1)
            if len(parts) == 1:
                items[parts[0]] = tkeys.TorrentFile(
                    tid=torrent_id, id=entry['id'],
                    name=entry['name'], path=path,
                    size_total=entry['length'],
                    size_downloaded=entry['bytesCompleted'],
                    is_wanted=entry['wanted'],
                    priority=entry['priority'])

            elif len(parts) == 2:
                subfolder, subpath = parts
                if subfolder not in subfolders:
                    subfolders[subfolder] = []
                entry['name'] = subpath
                subfolders[subfolder].append(entry)
            else:
                raise RuntimeError(parts)

        for subfolder,entries in subfolders.items():
            items[subfolder] = TorrentFileTree(torrent_id, entries, path=path+[subfolder])
        self._items = items

    def update(self, fileStats):
        def update_files(ftree, fileStats):
            if not fileStats:
                # If fileStats is empty (e.g. no metadata yet), there is a dummy
                # entry in ftree created by _create_TorrentFileTree().
                return

            for entry in ftree.values():
                if isinstance(entry, tkeys.TorrentFile):
                    # File ID is its index in the list provided by
                    # Transmission (see _create_TorrentFileTree)
                    fstats = fileStats[entry['id']]
                    entry.update({'size-downloaded': fstats['bytesCompleted'],
                                  'is-wanted': fstats['wanted'],
                                  'priority': fstats['priority']})
                else:
                    update_files(entry, fileStats)

        update_files(self._items, fileStats)


class TrackerList(tuple):
    def __new__(cls, raw_torrent):
        return super().__new__(cls,
            ({'id': tracker['id'],
              'url-announce': utils.URL(tracker['announce'])}
             for tracker in raw_torrent['trackers'])
        )


class PeerList(tuple):
    def __new__(cls, raw_torrent):
        TorrentPeer = tkeys.TorrentPeer
        return super().__new__(cls,
            (TorrentPeer(tid=raw_torrent['id'], tname=raw_torrent['name'],
                         tsize=raw_torrent['totalSize'],
                         ip=p['address'], port=p['port'], client=p['clientName'],
                         progress=p['progress']*100,
                         rate_up=p['rateToPeer'], rate_down=p['rateToClient'])
             for p in raw_torrent['peers'])
        )


# Map our keys to tuples of needed RPC field names for those keys
DEPENDENCIES = {
    'id'                : ('id',),
    'hash'              : ('hashString',),
    'name'              : ('name',),
    'ratio'             : ('uploadRatio',),
    'status'            : ('status', 'percentDone', 'metadataPercentComplete', 'rateDownload',
                           'rateUpload', 'peersConnected', 'trackerStats', 'isPrivate'),
    'path'              : ('downloadDir',),
    'private'           : ('isPrivate',),

    '%downloaded'       : ('percentDone',),
    '%metadata'         : ('metadataPercentComplete',),
    '%verified'         : ('recheckProgress',),

    'peers-connected'   : ('peersConnected',),
    'peers-uploading'   : ('peersSendingToUs',),
    'peers-downloading' : ('peersGettingFromUs',),
    'peers-seeding'     : ('trackerStats',),

    'timestamp-created' : ('dateCreated',),
    'timestamp-added'   : ('addedDate',),
    'timestamp-started' : ('startDate',),
    'timestamp-active'  : ('activityDate',),
    'timestamp-done'    : ('doneDate',),
    'timespan-eta'      : ('eta',),
    'timestamp-manual-announce-allowed': ('manualAnnounceTime',),

    'rate-down'         : ('rateDownload',),
    'rate-up'           : ('rateUpload',),

    'size-final'        : ('sizeWhenDone',),
    'size-total'        : ('totalSize',),
    'size-downloaded'   : ('downloadedEver',),
    'size-uploaded'     : ('uploadedEver',),
    'size-available'    : ('desiredAvailable',),
    'size-corrupt'      : ('corruptEver',),

    'trackers'          : ('trackers',),
    'peers'             : ('peers', 'totalSize'),

    # 'files' is called once to initialize file names and sizes by
    # api_torrent.TorrentAPI._get_torrents_by_ids when files are requested.
    'files'             : ('fileStats',),
}

# Map our keys to callables that adjust the raw RPC values or create new
# values from existing RPC values.
_MODIFY = {
    '%downloaded'     : lambda raw: raw['percentDone']*100,
    '%metadata'       : lambda raw: raw['metadataPercentComplete']*100,
    '%verified'       : lambda raw: raw['recheckProgress']*100,
    'status'          : _make_status,
    'peers-seeding'   : _count_seeds,
    'ratio'           : _modify_ratio,
    'timespan-eta'    : _modify_eta,
    'trackers'        : TrackerList,
    'peers'           : PeerList,
    'files'           : _create_TorrentFileTree,
}

class Torrent(base.TorrentBase):
    """Information about a torrent as a mapping

    The available keys are specified in DEPENDENCIES and tkeys.TYPES.
    """

    def __init__(self, raw_torrent):
        self._raw = raw_torrent
        self._cache = {}

    def update(self, raw_torrent):
        old = self._raw
        cache = self._cache

        # Update an existing TorrentFileTree instead of creating a new one
        if 'fileStats' in raw_torrent and 'files' in cache:
            cache['files'].update(raw_torrent.pop('fileStats'))
            # Make sure the 'files' RPC field doesn't exist because it always
            # triggers the creation of a new TorrentFileTree (see
            # _create_TorrentFileTree).
            if 'files' in raw_torrent:
                del raw_torrent['files']

        # Remove cached values if their original/raw value(s) differ
        for k,v in tuple(cache.items()):
            fields = DEPENDENCIES[k]
            for field in fields:
                if field in old and field in raw_torrent and \
                   old[field] != raw_torrent[field]:
                    del cache[k]
                    break
        old.update(raw_torrent)

    def __getitem__(self, key):
        cache = self._cache
        if key not in cache:
            raw = self._raw
            if key in _MODIFY:
                # Modifier gets the whole raw torrent
                value = _MODIFY[key](raw)
            else:
                fields = DEPENDENCIES[key]
                assert len(fields) == 1
                try:
                    value = raw[fields[0]]
                except KeyError:
                    raise KeyError(key)
            cache[key] = tkeys.TYPES[key](value)
        return cache[key]

    def __contains__(self, key):
        deps = DEPENDENCIES
        raw = self._raw
        # Check if key is known
        if key not in deps:
            return False
        else:
            # Check if we have all dependencies for key
            for dep in deps[key]:
                if dep not in raw:
                    return False
        return True

    def __iter__(self):
        for key in DEPENDENCIES:
            if key in self:
                yield key

    def __eq__(self, other):
        if isinstance(other, int):
            return self._raw['id'] == other
        elif isinstance(other, Torrent):
            return self._raw['id'] == other._raw['id']
        else:
            return NotImplemented

    def __lt__(self, other):
        return self._raw['id'] > other._raw['id']

    def __hash__(self):
        return hash(self._raw['id'])

    def clearcache(self):
        self._cache = {}


class TorrentFields(tuple):
    """Convert Torrent keys to those specified in rpc-spec.txt

    The resulting tuple has no duplicates and the keys 'id' and 'name' are
    always included.
    """
    _RPC_FIELDS = ('activityDate', 'addedDate', 'announceResponse', 'announceURL',
                   'bandwidthPriority', 'comment', 'corruptEver', 'creator',
                   'dateCreated', 'desiredAvailable', 'doneDate', 'downloadDir',
                   'downloadedEver', 'downloadLimit', 'downloadLimited',
                   'downloadLimitMode', 'error', 'errorString', 'eta', 'etaIdle',
                   'hashString', 'haveUnchecked', 'haveValid', 'honorsSessionLimits',
                   'id', 'isFinished', 'isPrivate', 'isStalled', 'lastAnnounceTime',
                   'lastScrapeTime', 'leftUntilDone', 'magnetLink',
                   'manualAnnounceTime', 'maxConnectedPeers',
                   'metadataPercentComplete', 'name', 'nextAnnounceTime',
                   'nextScrapeTime', 'peer-limit', 'peersConnected',
                   'peersGettingFromUs', 'peersSendingToUs', 'percentDone',
                   'pieceCount', 'pieceSize', 'queuePosition', 'rateDownload',
                   'rateUpload', 'recheckProgress', 'secondsDownloading',
                   'secondsSeeding', 'scrapeResponse', 'scrapeURL', 'seedIdleLimit',
                   'seedIdleMode', 'seedRatioLimit', 'seedRatioMode', 'sizeWhenDone',
                   'startDate', 'status', 'totalSize', 'torrentFile', 'uploadedEver',
                   'uploadLimit', 'uploadLimitMode', 'uploadLimited', 'uploadRatio',
                   'webseedsSendingToUs',

                   # Lists
                   'files', 'fileStats', 'peers', 'peersFrom', 'pieces', 'priorities',
                   'trackers', 'trackerStats', 'wanted', 'webseeds')

    _ALL_FIELDS = tuple(set(field
                            for fields in DEPENDENCIES.values()
                            for field in fields))
    _cache = {}

    def __new__(cls, *keys):
        if keys not in cls._cache:
            cls._cache[keys] = super().__new__(cls, cls._get_fields(*keys))
        return cls._cache[keys]

    @classmethod
    def _get_fields(cls, *keys):
        collected_fields = set(('id',))
        for key in keys:
            if key.lower() == 'all':
                return cls._ALL_FIELDS
            elif key in DEPENDENCIES:
                # key is one of Torrent's keys that needs one or more RPC fields
                collected_fields.update(DEPENDENCIES[key])
            elif key in cls._RPC_FIELDS:
                # key is a valid Transmission RPC field
                collected_fields.add(key)
            else:
                raise ValueError('Unknown torrent key: {!r}'.format(key))
        return collected_fields

    def __add__(self, other):
        if isinstance(other, (type(self), set, list, tuple)):
            fields = set(self)  # Make a copy
            fields.update(other)
            return type(self)(*fields)
        else:
            return NotImplemented

    def __eq__(self, other):
        return set(self) == set(other)

    def __ne__(self, other):
        return not self.__eq__(other)
