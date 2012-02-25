import logging
import webcom
import mutagen
import time
import config
from random import randint
from multiprocessing import RLock
REGULAR = 0
REQUEST = 1

class EmptyQueue(Exception):
    pass

# TO DO
# Make sure the queue times are correct after adding a request
# Check string encoding ? seems to be non-unicode string returned
# Fix encoding on all metadata
# Make regular queue go empty when requests get entered
class queue(object):
    _lock = RLock()
    @staticmethod
    def get_timestamp(cur, type=REGULAR):
        if (type == REGULAR):
            cur.execute("SELECT unix_timestamp(time) AS timestamp, length FROM `queue` ORDER BY `time` DESC LIMIT 1;")
        elif (type == REQUEST):
            cur.execute("SELECT unix_timestamp(time) AS timestamp, length FROM `queue` WHERE type={type} ORDER BY `time` DESC LIMIT 1;"\
                        .format(type=type))
        if (cur.rowcount > 0):
            result = cur.fetchone()
            return result['timestamp'] + int(result['length'])
        else:
            return np.end()
    def append_request(self, song, ip="0.0.0.0"):
        with webcom.MySQLCursor(lock=self._lock) as cur:
            timestamp = self.get_timestamp(cur, REQUEST)
            cur.execute("UPDATE `queue` SET time=from_unixtime(\
                            unix_timestamp(time) + %s) WHERE type=0;",
                            (song.length,))
            cur.execute("DELETE FROM `queue` WHERE type=0 \
                            ORDER BY time DESC LIMIT 1")
            cur.execute("INSERT INTO `queue` (trackid, time, ip, \
            type, meta, length) VALUES (%s, from_unixtime(%s), %s, %s, %s, %s);",
                        (song.id, int(timestamp), ip, REQUEST,
                          song.metadata, song.length))
  
    def append(self, song):
        with webcom.MySQLCursor(lock=self._lock) as cur:
            timestamp = self.get_timestamp(cur, REGULAR)
            cur.execute("INSERT INTO `queue` (trackid, time, type, meta, \
            length) VALUES (%s, from_unixtime(%s), %s, %s, %s);",
                        (song.id, int(timestamp), REGULAR,
                          song.metadata, song.length))
    def append_many(self, songlist):
        """queue should be an iterater containing
            Song objects
        """
        with webcom.MySQLCursor(lock=self._lock) as cur:
            timestamp = self.get_timestamp(cur)
            for song in songlist:
                if (song.afk):
                    cur.execute(
                                "INSERT INTO `queue` (trackid, time, meta, \
                                length) VALUES (%s, \
                                from_unixtime(%s), %s, %s);",
                                (song.id, int(timestamp),
                                  song.metadata, song.length)
                                )
                    timestamp += song.length
                else:
                    cur.execute(
                                "INSERT INTO `queue` (time, meta, length) \
                                VALUES (from_unixtime(%s), %s, \
                                %s);",
                                (int(timestamp), song.metadata, song.length)
                                )
                    timestamp += length

    def append_random(self, amount=10):
        """Appends random songs to the queue,
        these come from the tracks table in
        the database"""
        if (amount > 100):
            amount = 100
        with webcom.MySQLCursor(lock=self._lock) as cur:
            cur.execute("SELECT tracks.id AS trackid \
            FROM tracks WHERE `usable`=1 AND NOT EXISTS (SELECT 1 FROM queue \
            WHERE queue.trackid = tracks.id) ORDER BY `lastplayed` ASC, \
            `lastrequested` ASC LIMIT 100;")
            result = list(cur.fetchall())
            queuelist = []
            n = 99
            for i in xrange(amount):
                row = result.pop(randint(0, n))
                queuelist.append(Song(id=row['trackid']))
                n -= 1
        self.append_many(queuelist)
    def pop(self):
        try:
            with webcom.MySQLCursor(lock=self._lock) as cur:
                cur.execute("SELECT * FROM `queue` ORDER BY `time` ASC LIMIT 1;")
                if (cur.rowcount > 0):
                    result = cur.fetchone()
                    cur.execute("DELETE FROM `queue` WHERE id={id};"\
                                .format(id=result['id']))
                    return Song(id=result['trackid'],
                                meta=result['meta'],
                                length=result['length'])
                else:
                    raise EmptyQueue("Queue is empty")
        finally:
            if (self.length < 20):
                self.append_random(20 - self.length)
    def clear(self):
        with webcom.MySQLCursor(lock=self._lock) as cur:
            cur.execute("DELETE FROM `queue`;")
    @property
    def length(self):
        return len(self)
    def __len__(self):
        with webcom.MySQLCursor(lock=self._lock) as cur:
            cur.execute("SELECT COUNT(*) as count FROM `queue`;")
            return int(cur.fetchone()['count'])
    def __iter__(self):
        with webcom.MySQLCursor(lock=self._lock) as cur:
            cur.execute("SELECT * FROM `queue` ORDER BY `time` ASC LIMIT 5;")
            for row in cur:
                yield Song(id=row['trackid'],
                           meta=row['meta'].decode('utf-8'),
                           length=row['length'])

class lp(object):
    def get(self, amount=5):
        return list(self.iter(amount))
    def iter(self, amount=5):
        if (not isinstance(amount, int)):
            pass
        with webcom.MySQLCursor() as cur:
            cur.execute("SELECT esong.meta FROM eplay JOIN esong ON \
            esong.id = eplay.isong ORDER BY eplay.dt DESC LIMIT %s;",
            (amount,))
            for row in cur:
                yield Song(meta=row['meta'])
    def __iter__(self):
        return self.iter()

class status(object):
    _timeout = time.time() - 60
    @property
    def listeners(self):
        return int(self.cached_status.get('Current Listeners', 0))
    @property
    def peak_listeners(self):
        return int(self.cached_status.get('Peak Listeners', 0))
    @property
    def online(self):
        return config.icecast_mount in self.status
    @property
    def started(self):
        return self.cached_status.get("Mount started", "Unknown")
    @property
    def type(self):
        return self.cached_status.get("Content Type", None)
    @property
    def current(self):
        return self.cached_status.get("Current Song", u"")
    @property
    def cached_status(self):
        import streamstatus
        if (time.time() - self._timeout > 9):
            self._status = streamstatus.get_status(config.icecast_server)
            self._timeout = time.time()
        return self._status[config.icecast_mount]
    @property
    def status(self):
        import streamstatus
        self._status = streamstatus.get_status(config.icecast_server)
        self._timeout = time.time()
        return self._status
    def update(self):
        """Updates the database with current collected info"""
        with webcom.MySQLCursor() as cur:
            cur.execute(
                        "UPDATE `streamstatus` SET `lastset`=NOW(), \
                        `np`=%s, `djid`=%s, `listeners`=%s, \
                        `start_time`=%s, `end_time`=%s, \
                        `isafkstream`=%s WHERE `id`=0;",
                        (np.metadata, dj.id, self.listeners,
                         np._start, np.end(),
                         1 if np.afk else 0)
                        )
class np(object):
    _end = 0
    _start = int(time.time())
    def __init__(self):
        from threading import Thread
        self.song = Song(meta=u"", length=0.0)
        self.updater = Thread(target=self.send)
        self.updater.daemon = 1
        self.updater.start()
    def send(self):
        while True:
            if (status.online):
                status.update()
            time.sleep(10)
    def change(self, song):
        """Changes the current playing song to 'song' which should be an
        manager.Song object"""
        if (self.song.metadata != u""):
            self.song.update(lp=time.time())
            if (self.song.length == 0):
                self.song.update(length=(time.time() - self._start))
        self.song = song
        self._start = int(time.time())
        self._end = int(time.time()) + self.song.length
    def remaining(self, remaining):
        self.song.update(length=(time.time() + remaining) - self._start)
        self._end = time.time() + remaining
    def end(self):
        return self._end if self._end != 0 else int(time.time())
    def __getattr__(self, name):
        return getattr(self.song, name)
    def __repr__(self):
        return "<Playing " + repr(self.song)[1:]
    def __str__(self):
        return self.__repr__()

class DJError(Exception):
    pass
class dj(object):
    _name = None
    _cache = {}
    def g_id(self):
        user = self.user
        if (user in self._cache):
            return self._cache[user]
        with webcom.MySQLCursor() as cur:
            # we don't have a user
            if (not self.user):
                cur.execute("SELECT `djid` FROM `streamstatus`")
                djid = cur.fetchone()['djid']
                cur.execute("SELECT `user` FROM `users` WHERE `djid`=%s \
                LIMIT 1;", (djid,))
                if (cur.rowcount > 0):
                    user = cur.fetchone()['user']
                    self._cache[user] = djid
                    self._name = user
                return djid
            
            cur.execute("SELECT `djid` FROM `users` WHERE `user`=%s LIMIT 1;",
                        (user,))
            if cur.rowcount > 0:
                djid = cur.fetchone()['djid']
                if djid != None:
                    self._cache[user] = djid
                    return djid
            return 0
    def s_id(self, value):
        if (not isinstance(value, (int, long, float))):
            raise TypeError("Expected integer")
        with webcom.MySQLCursor() as cur:
            cur.execute("SELECT `user` FROM `users` WHERE `djid`=%s \
            LIMIT 1;", (value,))
            if (cur.rowcount > 0):
                user = cur.fetchone()['user']
                self._cache[user] = djid
                self._name = user
            else:
                raise TypeError("Invalid ID, no such DJ")
    id = property(g_id, s_id)
    def g_name(self):
        return self._name
    def s_name(self, value):
        old_name = self._name
        self._name = value
        if (self.user == None):
            self._name = old_name
            raise TypeError("Invalid name, no such DJ")
    name = property(g_name, s_name)
    @property
    def user(self):
        from re import escape, search, IGNORECASE
        name = self.name
        if (name == None):
            with webcom.MySQLCursor() as cur:
                cur.execute("SELECT `djid` FROM `streamstatus`")
                djid = cur.fetchone()['djid']
                cur.execute("SELECT `user` FROM `users` WHERE `djid`=%s \
                LIMIT 1;", (djid,))
                if (cur.rowcount > 0):
                    user = cur.fetchone()['user']
                    self._cache[user] = djid
                    self._name = user
                    name = user
                else:
                    return None
        with open(config.djfile) as djs:
            djname = None
            for line in djs:
                temp = line.split('@')
                wildcards = temp[0].split('!')
                djname_temp = temp[1].strip()
                for wildcard in wildcards:
                    wildcard = escape(wildcard)
                    '^' + wildcard
                    wildcard = wildcard.replace('*', '.*')
                    if (search(wildcard, name, IGNORECASE)):
                        djname = djname_temp
                        break
                if (djname):
                    return unicode(djname)
        return None

class Song(object):
    def __init__(self, id=None, meta=None, length=None, filename=None):
        if (not isinstance(id, (int, long, type(None)))):
            raise TypeError("'id' incorrect type, expected int or long")
        if (not isinstance(meta, (basestring, type(None)))):
            raise TypeError("'meta' incorrect type, expected string or unicode")
        if (not isinstance(length, (int, long, float, type(None)))):
            raise TypeError("'length' incorrect type, expected int or long")
        if (not isinstance(filename, (basestring, type(None)))):
            raise TypeError("'filename' incorrect type, expected string or unicode")
        self._length = length
        self._id = id
        self._digest = None
        self._lp = None
        self._songid = None
        self._faves = None
        if (meta is None) and (self.id == 0L):
            raise TypeError("Require either 'id' or 'meta' argument")
        elif (self.id != 0L):
            temp_filename, temp_meta = self.get_file(self.id)
            if (meta == None):
                meta = temp_meta
            if (filename == None):
                filename = temp_filename
        self._filename = filename
        self._metadata = self.fix_encoding(meta)
    def update(self, **kwargs):
        """Gives you the possibility to update the
            'lp', 'id', 'length', 'filename' and 'metadata'
            variables in the Song instance
            
            Updating the 'lp' and 'length' will directly affect the database
            while 'filename', 'metadata' and 'id' don't, updating 'id' also
            updates 'filename' but not 'metadata'
            """
        if (self.metadata == u'') and (kwargs.get("metadata", u"") == u""):
            return
        for key, value in kwargs.iteritems():
            if (key in ["lp", "id", "length", "filename", "metadata"]):
                setattr(self, "_" + key, value)
                with webcom.MySQLCursor() as cur:
                    if (key == "lp"):
                        # change database entries for LP data
                        cur.execute("INSERT INTO eplay ('isong', 'dt') \
                        VALUES(%s, FROM_UNIXTIME(%s));",
                        (self.songid, self.lp))
                        if (self.afk):
                            cur.execute("UPDATE `tracks` SET \
                            `lastplayed`=FROM_UNIXTIME(%s) \
                            WHERE `id`=%s LIMIT 1;", (self.lp, self.id))
                    elif (key == "length"):
                        # change database entries for length data
                        cur.execute("UPDATE `esong` SET `len`=%s WHERE \
                        id=%s", (self.length, self.songid))
                    elif (key == "id"):
                        self._filename, temp = self.get_file(value)
    @staticmethod
    def create_digest(metadata):
        """Creates a digest of 'metadata'"""
        from hashlib import sha1
        if (type(metadata) == unicode):
            metadata = metadata.encode('utf-8', 'replace')
        return sha1(metadata).hexdigest()
    @property
    def filename(self):
        """Filename, returns None if none found"""
        return self._filename if self._filename != None else None
    @property
    def id(self):
        """Returns the trackid, as in tracks.id"""
        return self._id if self._id != None else 0L
    @property
    def songid(self):
        """Returns the songid as in esong.id, efave.isong, eplay.isong"""
        if (not self._songid):
            self._songid = self.get_songid(self)
        return self._songid
    @property
    def metadata(self):
        """Returns metadata or an empty unicode string"""
        return self._metadata if self._metadata != None else u''
    @property
    def digest(self):
        """A sha1 digest of the metadata, can be changed by updating the
        metadata"""
        if (self._digest == None):
            self._digest = self.create_digest(self.metadata)
        return self._digest
    @property
    def length(self):
        """Returns the length from song as integer, defaults to 0"""
        if (self._length == None):
            self._length = self.get_length(self)
        return int(self._length if self._length != None else 0)
    @property
    def lengthf(self):
        """Returns the length formatted as mm:nn where mm is minutes and
        nn is seconds, defaults to 00:00. Returns an unicode string"""
        return u'%02d:%02d' % divmod(self.length, 60)
    @property
    def lp(self):
        """Returns the unixtime of when this song was last played, defaults
        to None"""
        with webcom.MySQLCursor() as cur:
            query = "SELECT unix_timestamp(`dt`) AS ut FROM eplay,esong \
            WHERE eplay.isong = esong.id AND esong.hash = '{digest}' \
            ORDER BY `dt` DESC LIMIT 1;"
            cur.execute(query.format(digest=self.digest))
            if (cur.rowcount > 0):
                return cur.fetchone()['ut']
            return None
    @property
    def lpf(self):
        """Returns a unicode string of when this song was last played,
        looks like '5 years, 3 months, 1 week, 4 days, 2 hours,
         54 minutes, 20 seconds', defaults to 'Never before'"""
        return parse_lastplayed(0 if self.lp == None else self.lp)
    @property
    def favecount(self):
        """Returns the amount of favorites on this song as integer,
        defaults to 0"""
        return len(self.faves)
    @property
    def faves(self):
        """Returns a Faves instance, list-like object that allows editing of
        the favorites of this song"""
        class Faves(object):
            def __init__(self, song):
                self.song = song
            def index(self, key):
                """Same as a normal list, very inefficient shouldn't be used"""
                return list(self).index(key)
            def count(self, key):
                """returns 1 if nick exists else 0, use "key in faves" instead
                of faves.count(key)"""
                if (key in self):
                    return 1
                return 0
            def remove(self, key):
                """Removes 'key' from the favorites"""
                self.__delitem__(key)
            def pop(self, index):
                """Not implemented"""
                raise NotImplemented("No popping allowed")
            def insert(self, index, value):
                """Not implemented"""
                raise NotImplemented("No inserting allowed, use append")
            def sort(self, cmp, key, reverse):
                """Not implemented"""
                raise NotImplemented("Sorting now allowed, use reverse(faves) or list(faves)")
            def append(self, nick):
                """Add a nickname to the favorites of this song, handles
                creation of nicknames in the database. Does nothing if
                nick is already in the favorites"""
                if (nick in self):
                    return
                with webcom.MySQLCursor() as cur:
                    cur.execute("SELECT * FROM enick WHERE nick=%s;",
                                (nick,))
                    if (cur.rowcount == 0):
                        cur.execute("INSERT INTO enick (`nick`) VALUES(%s);",
                                    (nick,))
                        cur.execute("SELECT * FROM enick WHERE nick=%s;",
                                    (nick,))
                        nickid = cur.fetchone()['id']
                        cur.execute("INSERT INTO efave (`inick`, `isong`) \
                        VALUES(%s, %s);", (nickid, self.song.songid))
                    elif (cur.rowcount == 1):
                        nickid = cur.fetchone()['id']
                        cur.execute("INSERT INTO efave (inick, isong) \
                        VALUES(%s, %s);", (nickid, self.song.songid))
                    if (self.song.id != 0L):
                        cur.execute("UPDATE `tracks` SET `priority`=priority+2\
                         WHERE `id`=%s;", (self.song.id,))
            def extend(self, seq):
                """Same as 'append' but allows multiple nicknames to be added
                by suppling a list of nicknames"""
                original = list(self)
                with webcom.MySQLCursor() as cur:
                    for nick in seq:
                        if (nick in original):
                            continue
                        original.append(nick)
                        cur.execute("SELECT * FROM enick WHERE nick=%s;",
                                (nick,))
                        if (cur.rowcount == 0):
                            cur.execute("INSERT INTO enick (`nick`) VALUES(%s);",
                                        (nick,))
                            cur.execute("SELECT * FROM enick WHERE nick=%s;",
                                        (nick,))
                            nickid = cur.fetchone()['id']
                            cur.execute("INSERT INTO efave (`inick`, `isong`) \
                            VALUES(%s, %s);", (nickid, self.song.songid))
                        elif (cur.rowcount == 1):
                            nickid = cur.fetchone()['id']
                            cur.execute("INSERT INTO efave (inick, isong) \
                            VALUES(%s, %s);", (nickid, self.song.songid))
                        if (self.song.id != 0L):
                            cur.execute("UPDATE `tracks` SET `priority`=\
                            priority+2 WHERE `id`=%s;", (self.song.id,))
            def __iter__(self):
                """Returns an iterator over the favorite list, sorted 
                alphabetical. Use list(faves) to generate a list copy of the
                nicknames"""
                with webcom.MySQLCursor() as cur:
                    cur.execute("SELECT enick.nick FROM esong JOIN efave ON \
                    efave.isong = esong.id JOIN enick ON efave.inick = \
                    enick.id WHERE esong.hash = '{digest}' ORDER BY enick.nick\
                     ASC"\
                    .format(digest=self.song.digest))
                    for result in cur:
                        yield result['nick']
            def __reversed__(self):
                """Just here for fucks, does the normal as you expect"""
                with webcom.MySQLCursor() as cur:
                    cur.execute("SELECT enick.nick FROM esong JOIN efave ON \
                    efave.isong = esong.id JOIN enick ON efave.inick = \
                    enick.id WHERE esong.hash = '{digest}' ORDER BY enick.nick\
                     DESC"\
                    .format(digest=self.song.digest))
                    for result in cur:
                        yield result['nick']
            def __len__(self):
                """len(faves) is efficient"""
                with webcom.MySQLCursor() as cur:
                    cur.execute("SELECT count(*) AS favecount FROM efave \
                    WHERE isong={songid}".format(songid=self.song.songid))
                    return cur.fetchone()['favecount']
            def __getitem__(self, key):
                return list(self)[key]
            def __setitem__(self, key, value):
                """Not implemented"""
                raise NotImplemented("Can't set on <Faves> object")
            def __delitem__(self, key):
                original = list(self)
                if (isinstance(key, basestring)):
                    # Nick delete
                    if (key in original):
                        # It is in there
                        with webcom.MySQLCursor() as cur:
                            cur.execute(
        "DELETE efave.* FROM efave LEFT JOIN enick ON enick.id = efave.inick WHERE \
        enick.nick=%s AND isong=%s;", (key, self.song.songid))
                    else:
                        raise KeyError("{0}".format(key))
                elif (isinstance(key, (int, long))):
                    try:
                        key = original[key]
                    except (IndexError):
                        raise IndexError("Fave index out of range")
                    else:
                        with webcom.MySQLCursor() as cur:
                            cur.execute(
                                        "DELETE efave.* FROM efave LEFt JOIN \
                                        enick ON enick.id = efave.inick WHERE \
                                        enick.nick=%s AND isong=%s;",
                                        (key, self.song.songid))
                else:
                    raise TypeError("Fave key has to be 'string' or 'int'")
            def __contains__(self, key):
                with webcom.MySQLCursor() as cur:
                    cur.execute("SELECT count(*) AS contains FROM efave JOIN\
                     enick ON enick.id = efave.inick WHERE enick.nick=%s \
                     AND efave.isong=%s;",
                     (key, self.song.songid))
                    if (cur.fetchone()['contains'] > 0):
                        return True
                    return False
            def __repr__(self):
                return u"Favorites of %s" % repr(self.song)
            def __str__(self):
                return self.__repr__().encode('utf-8')
        if (not self._faves):
            return Faves(self)
        return self._faves

    @property
    def playcount(self):
        """returns the playcount as long, defaults to 0L"""
        with webcom.MySQLCursor() as cur:
            query = "SELECT count(*) AS playcount FROM eplay,esong WHERE \
            eplay.isong = esong.id AND esong.hash = '{digest}';"
            cur.execute(query.format(digest=self.digest))
            if (cur.rowcount > 0):
                return cur.fetchone()['playcount']
            else:
                return 0L
    @property
    def afk(self):
        """Returns true if there is an self.id, which means there is an
        entry in the 'tracks' table for this song"""
        return False if self.id == 0L else True
    @staticmethod
    def get_length(song):
        if (song.filename != None):
            try:
                length = mutagen.File(song.filename).info.length
            except (IOError):
                logging.exception("Failed length check")
                return 0.0
            return length
        if (song.filename == None):
            # try hash
            with webcom.MySQLCursor() as cur:
                cur.execute("SELECT len FROM `esong` WHERE `hash`=%s;",
                            (song.digest,))
                if (cur.rowcount > 0):
                    return cur.fetchone()['len']
                else:
                    return 0.0
    @staticmethod
    def get_file(songid):
        """Retrieve song path and metadata from the track ID"""
        from os.path import join
        with webcom.MySQLCursor() as cur:
            cur.execute("SELECT * FROM `tracks` WHERE `id`=%s LIMIT 1;" % (songid))
            if cur.rowcount == 1:
                row = cur.fetchone()
                artist = row['artist']
                title = row['track']
                path = join(config.music_directory, row['path'])
                meta = title if artist == u'' \
                        else artist + u' - ' + title
                return (path, meta)
            else:
                return (None, None)
    @staticmethod
    def get_songid(song):
        with webcom.MySQLCursor() as cur:
            cur.execute("SELECT * FROM `esong` WHERE `hash`=%s LIMIT 1;",
                        (song.digest,))
            if (cur.rowcount == 1):
                return cur.fetchone()['id']
            else:
                cur.execute("INSERT INTO `esong` (`hash`, `len`, `meta`) \
                VALUES (%s, %s, %s);", (song.digest, song.length, song.metadata))
                cur.execute("SELECT * FROM `esong` WHERE `hash`=%s LIMIT 1;",
                        (song.digest,))
                return cur.fetchone()['id']
    @staticmethod
    def fix_encoding(metadata):
        try:
            try:
                return unicode(metadata, 'utf-8', 'strict')
            except (UnicodeDecodeError):
                return unicode(metadata, 'shiftjis', 'replace')
        except (TypeError):
            return metadata
    @classmethod
    def search(cls, query, limit=5):
        """Searches the 'tracks' table in the database, returns a list of
        Song objects. Defaults to 5 results, can be less"""
        from re import compile, escape, sub
        def replace(query):
            re = compile("|".join(escape(s) for s in \
                                  {r"\\": "", r"(": "",
                                         r")": "", r"*": ""}))
            return re.sub(lambda x: replacements[x.group()], query)
        from os.path import join
        query_raw = query
        with webcom.MySQLCursor() as cur:
            search = replace(query)
            temp = []
            search = search.split(" ")
            for item in search:
                result = sub(r"^[+\-<>~]", "", item)
                temp.append("+" + result)
            query = " ".join(temp)
            del temp
            try:
                query = query.encode("utf-8")
                query_raw = query_raw.encode("utf-8")
            except (UnicodeDecodeError):
                return []
            cur.execute("SELECT * FROM `tracks` WHERE `usable`='1' AND MATCH \
            (tags, artist, track, album) AGAINST (%s IN BOOLEAN MODE) \
            ORDER BY `priority` DESC, MATCH (tags, artist, track, \
            album) AGAINST (%s) DESC LIMIT %s;",
                    (query, query_raw, limit))
        result = []
        for row in cur:
            result.append(cls(
                              id=row['id'],
                              meta=row['track'] if row['artist'] == u'' \
                                else row['artist'] + u' - ' + row['track'],
                            filename=join(config.music_directory, row['path'])))
        return result
    def __str__(self):
        return self.__repr__()
    def __repr__(self):
        return (u"<Song [%s, %d, %s] at %s>" % (self.metadata, self.id,
                                             self.digest, hex(id(self))))\
                                             .encode("utf-8")
        
# declaration goes here
status = status()
np = np()
dj = dj()
queue = queue()
lp = lp()
# GENERAL TOOLS GO HERE

def get_ms(self, seconds):
        m, s = divmod(seconds, 60)
        return u"%02d:%02d" % (m, s)
def parse_lastplayed(seconds):
    if (seconds > 0):
        difference = int(time.time()) - seconds
        year, month = divmod(difference, 31557600)
        month, week = divmod(month, 2629800)
        week, day = divmod(week, 604800)
        day, hour = divmod(day, 86400)
        hour, minute = divmod(hour, 3600)
        minute, second = divmod(minute, 60)
        result = ''
        if (year): result += u'%d year(s) ' % year
        if (month): result += u'%d month(s) ' % month
        if (week): result += u'%d week(s) ' % week
        if (day): result += u'%d day(s) ' % day
        if (hour): result += u'%d hour(s) ' % hour
        if (minute): result += u'%d minute(s) ' % minute
        if (second): result += u'%d second(s) ' % second
        return result.strip()
    else:
        return u'Never before'