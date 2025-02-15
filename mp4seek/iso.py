from math import ceil
import struct

import atoms
from atoms import read_fcc, read_ulong, read_ulonglong


def write_uchar(fobj, n):
    fobj.write(struct.pack('>B', n))

def write_ulong(fobj, n):
    fobj.write(struct.pack('>L', n))

def write_ulonglong(fobj, n):
    fobj.write(struct.pack('>Q', n))

def write_fcc(fobj, fcc_str):
    # print '[wfcc]: @%d %r' % (fobj.tell(), fcc_str)
    fobj.write('%-4.4s' % fcc_str)


def takeby(seq, n, force_tuples=False):
    if n == 1 and not force_tuples:
        return seq
    return [tuple(seq[i:i + n]) for i in xrange(0, len(seq), n)]

def read_table(f, row_spec, entries, spec_prefix='>'):
    """Read a continuous region of file and unpack it into a list of
    tuples using the given struct specification of a single row.

    @param row_spec: spec describing single row of table using same
                     syntax as in L{struct} module.
    @type  row_spec: str

    @param entries: number of rows to read
    @type  entries: int

    @param spec_prefix: optional specification that will be used for
                        the whole table
    @type  spec_prefix: str
    """
    if entries == 0:
        return []
    row_bytes = struct.calcsize('%s%s' % (spec_prefix, row_spec))
    data = f.read(row_bytes * entries)
    try:
        l = struct.unpack('%s%s' % (spec_prefix, row_spec * entries), data)
    except struct.error:
        raise RuntimeError('Not enough data: requested %d, read %d' %
                           (row_bytes * entries, len(data)))

    per_row = len(l) / entries
    return takeby(l, per_row)

class UnsuportedVersion(Exception):
    pass

class FormatError(Exception):
    pass

class CannotSelect(Exception):
    pass


class AttribInitializer(type):
    def __new__(meta, classname, bases, classdict):
        if '_fields' in classdict:
            fields = classdict['_fields']
            orig_init = classdict.pop('__init__', None)
            def __init__(self, *a, **kw):
                f_dict = {}
                for f in fields:
                    f_dict[f] = kw.pop(f, None)
                if orig_init:
                    self.__dict__.update(f_dict)
                    orig_init(self, *a, **kw)
                elif bases and bases[0] != object:
                    super(self.__class__, self).__init__(*a, **kw)
                    self.__dict__.update(f_dict)
            classdict['__init__'] = __init__
            if '__repr__' not in classdict:
                def __repr__(self):
                    r = '%s(%s)' % (self.__class__.__name__,
                                    ', '.join(['%s=%r' % (n, getattr(self, n))
                                               for n in fields]))
                    return r
                classdict['__repr__'] = __repr__
        return type.__new__(meta, classname, bases, classdict)

class Box(object):
    __metaclass__ = AttribInitializer
    def __init__(self, atom):
        self._atom = atom

    def get_size(self):
        # should be overriden in the boxes we want to be able to modify
        return self._atom.get_size()

    def get_offset(self):
        return self._atom.get_offset()

    def copy(self, *a, **kw):
        cls = self.__class__
        if getattr(self, '_fields', None):
            attribs = dict([(k, getattr(self, k)) for k in self._fields])
            attribs.update(dict([(k, kw[k]) for k in self._fields if k in kw]))
        else:
            attribs = {}
        return cls(self._atom, **attribs)

    def write(self, fobj):
        # print '[ b] writing:', self
        self._atom.write(fobj)

    def write_head(self, fobj):
        # assuming 'short' sizes for now - FIXME!
        # print '[ b] writing head:', self._atom
        a = self._atom
        write_ulong(fobj, self.get_size())
        write_fcc(fobj, a.type)
        if (a.extended_type):
            fobj.write(a.extended_type)

class FullBox(Box):
    def tabled_size(self, body_size, loop_size):
        # TODO: move to a separate TableFullBox subclass?
        return (self._atom.head_size_ext() + body_size +
                len(self.table) * loop_size)

    def write_head(self, fobj):
        Box.write_head(self, fobj)
        a = self._atom
        write_ulong(fobj, (a.v & 0xff) << 24 | (a.flags & 0xffffff))

class ContainerBox(Box):
    def __init__(self, *a, **kw):
        Box.__init__(self, *a, **kw)
        self._extra_children = []

    def get_size(self):
        # print '[>] getting size: %r' % self._atom
        fields = getattr(self, '_fields', [])
        cd = self._atom.get_children_dict()
        size = self._atom.head_size_ext()
        for k, v in cd.items():
            if k in fields:
                v = getattr(self, k)
                if not isinstance(v, (tuple, list)):
                    if v is None:
                        v = []
                    else:
                        v = [v]
            # print 'size for %r = %r' % (sum([a.get_size() for a in v]), v)
            size += sum([a.get_size() for a in v])
        size += sum([a.get_size() for a in self._extra_children])
        # print '[<] getting size: %r = %r' % (self._atom, size)
        return size

    def write(self, fobj):
        self.write_head(fobj)

        fields = getattr(self, '_fields', [])
        cd = self._atom.get_children_dict()
        to_write = []
        for k, v in cd.items():
            if k in fields:
                v = getattr(self, k)
                if not isinstance(v, (tuple, list)):
                    if v is None:
                        v = []
                    else:
                        v = [v]
            to_write.extend(v)

        def _get_offset(a):
            return a.get_offset()

        to_write.sort(key=_get_offset)
        to_write.extend(self._extra_children)

        # print '[  ] going to write:', \
        #     ([(isinstance(a, Box) and a._atom.type or a.type)
        #       for a in to_write])
        for ca in to_write:
            # print '[cb] writing:', ca
            ca.write(fobj)

    def add_extra_children(self, al):
        self._extra_children.extend(al)

def fullboxread(f):
    def _with_full_atom_read_wrapper(cls, a):
        return f(cls, atoms.full(a))
    return _with_full_atom_read_wrapper

def containerboxread(f):
    def _with_container_atom_read_wrapper(cls, a):
        return f(cls, atoms.container(a))
    return _with_container_atom_read_wrapper

def ver_skip(atom, sizes):
    if atom.v > len(sizes) or atom.v < 0:
        raise UnsuportedVersion('version requested: %d' % atom.v)
    atom.skip(sizes[atom.v])

def ver_read(atom, readers):
    if atom.v > len(readers) or atom.v < 0:
        raise UnsuportedVersion('version requested: %d' % atom.v)
    return readers[atom.v](atom.f)

def maybe_build_atoms(atype, alist):
    cls = globals().get(atype)
    if cls and issubclass(cls, Box):
        return map(cls.read, alist)
    return alist

def select_children_atoms(a, *selection):
    return select_atoms(a.get_children_dict(), *selection)

def select_atoms(ad, *selection):
    """ad: atom dict
    selection: [(type, min_required, max_required), ...]"""
    selected = []
    for atype, req_min, req_max in selection:
        alist = ad.get(atype, [])
        found = len(alist)
        if ((req_min is not None and found < req_min) or
            (req_max is not None and found > req_max)):
            raise CannotSelect('requested number of atoms %r: in [%s; %s],'
                               ' found: %d (all children: %r)' %
                               (atype, req_min, req_max, found, ad))
        alist = maybe_build_atoms(atype, alist)
        if req_max == 1:
            if found == 0:
                selected.append(None)
            else:
                selected.append(alist[0])
        else:
            selected.append(alist)
    return selected

def find_atom(alist, type):
    return [a.type for a in alist].index(type)

def write_atoms(alist, f):
    # alist - list of Atoms or Boxes
    for a in alist:
        a.write(f)

def find_samplenum_stts(stts, mt):
    "stts - table of the 'stts' atom; mt - media time"
    ctime = 0
    samples = 1
    i, n = 0, len(stts)
    while i < n:
        # print 'fsstts:', mt, ctime, stts[i], samples, ctime
        if mt == ctime:
            break
        count, delta = stts[i]
        cdelta = count * delta
        if mt < ctime + cdelta:
            samples += int(ceil((mt - ctime) / float(delta)))
            break
        ctime += cdelta
        samples += count
        i += 1

    return samples

def find_mediatime_stts(stts, sample):
    ctime = 0
    samples = 1
    i, n = 0, len(stts)
    while i < n:
        count, delta = stts[i]
        if samples + count >= sample:
            return ctime + (sample - samples) * delta
        ctime += count * delta
        samples += count
        i += 1
    return ctime

def find_mediatimes(stts, samples):
    ctime = 0
    total_samples = 1
    ret = []
    i, n = 0, len(stts)
    j, m = 0, len(samples)
    while i < n and j < m:
        count, delta = stts[i]
        sample = samples[j]
        if total_samples + count >= sample:
            ret.append(ctime + (sample - total_samples) * delta)
            j += 1
            continue
        ctime += count * delta
        total_samples += count
        i += 1
    return ret

def find_chunknum_stsc(stsc, sample_num):
    current = 1                 # 1-based indices!
    per_chunk = 0
    samples = 1
    i, n = 0, len(stsc)
    while i < n:
        # print 'fcnstsc:', sample_num, current, stsc[i], samples, per_chunk
        next, next_per_chunk, _sdidx = stsc[i]
        samples_here = (next - current) * per_chunk
        if samples + samples_here > sample_num:
            break
        samples += samples_here
        current, per_chunk = next, next_per_chunk
        i += 1
    return int((sample_num - samples) // per_chunk + current)

def get_chunk_offset(stco64, chunk_num):
    # 1-based indices!
    return stco64[chunk_num - 1]

class uuid(FullBox):
    _extended_type = None
    @classmethod
    def read(cls, a):
        # TODO implement a lookup of child classes based on _extended_type?
        raise Exception("not implemented yet")

class uuid_sscurrent(uuid):
    _fields = ('timestamp', 'duration')
    _extended_type = "\x6d\x1d\x9b\x05\x42\xd5\x44\xe6\x80\xe2\x14\x1d\xaf\xf7\x57\xb2"

    def write(self, fobj):
        self.write_head(fobj)
        write_ulonglong(fobj, self.timestamp)
        write_ulonglong(fobj, self.duration)

    def get_size(self):
        size = self._atom.head_size_ext()
        size += 2 * 8
        return size

    @classmethod
    def read(cls, a):
        raise Exception("not implemented yet")

    @classmethod
    def make(cls, timestamp, duration):
        a = atoms.FullAtom(0, "uuid", 0, 1, 0, None, extended_type=cls._extended_type)
        s = cls(a)
        s.timestamp = timestamp
        s.duration = duration
        return s

class uuid_ssnext(FullBox):
    _fields = ('entries')
    _extended_type = "\xd4\x80\x7e\xf2\xca\x39\x46\x95\x8e\x54\x26\xcb\x9e\x46\xa7\x9f"

    def write(self, fobj):
        self.write_head(fobj)
        write_uchar(fobj, len(self.entries))
        for ts, duration in self.entries:
            write_ulonglong(fobj, ts)
            write_ulonglong(fobj, duration)

    def get_size(self):
        size = self._atom.head_size_ext()
        size += 1 + (2 * 8) * len(self.entries)
        return size

    @classmethod
    def read(cls, a):
        raise Exception("not implemented yet")

    @classmethod
    def make(cls, entries):
        a = atoms.FullAtom(0, "uuid", 0, 1, 0, None, extended_type=cls._extended_type)
        s = cls(a)
        s.entries = entries
        return s

class mvhd(FullBox):
    _fields = (
        # 'creation_time', 'modification_time',
        'timescale', 'duration',
        # 'rate', 'volume', 'matrix', 'next_track_ID'
        )

    @classmethod
    @fullboxread
    def read(cls, a):
        ver_skip(a, (8, 16))
        ts = read_ulong(a.f)
        d = ver_read(a, (read_ulong, read_ulonglong))
        return cls(a, timescale=ts, duration=d)

    def write(self, fobj):
        self.write_head(fobj)
        a = self._atom
        a.seek_to_start()
        a.skip(a.head_size_ext())

        if a.v == 0:
            fobj.write(a.read_bytes(8))
            write_ulong(fobj, self.timescale)
            write_ulong(fobj, self.duration)
            a.skip(8)
        elif a.v == 1:
            fobj.write(a.read_bytes(16))
            write_ulong(fobj, self.timescale)
            write_ulonglong(fobj, self.duration)
            a.skip(12)
        else:
            raise RuntimeError()

        fobj.write(a.read_bytes(80))

class tkhd(FullBox):
    _fields = ('duration', 'id')

    @classmethod
    @fullboxread
    def read(cls, a):
        ver_skip(a, (8, 16))
        id = read_ulong(a.f)
        a.skip(4)
        d = ver_read(a, (read_ulong, read_ulonglong))
        return cls(a, duration=d, id=id)

    def write(self, fobj):
        self.write_head(fobj)
        a = self._atom
        a.seek_to_start()
        a.skip(a.head_size_ext())

        if a.v == 0:
            fobj.write(a.read_bytes(8))
            write_ulong(fobj, self.id)
            a.skip(4)
            fobj.write(a.read_bytes(4))
            write_ulong(fobj, self.duration)
            a.skip(4)
        elif a.v == 1:
            fobj.write(a.read_bytes(16))
            write_ulong(fobj, self.id)
            a.skip(4)
            fobj.write(a.read_bytes(4))
            write_ulonglong(fobj, self.duration)
            a.skip(8)
        else:
            raise RuntimeError()

        fobj.write(a.read_bytes(60))

class mdhd(FullBox):
    _fields = ('timescale', 'duration')

    @classmethod
    @fullboxread
    def read(cls, a):
        ver_skip(a, (8, 16))
        ts = read_ulong(a.f)
        d = ver_read(a, (read_ulong, read_ulonglong))
        return cls(a, timescale=ts, duration=d)

    def write(self, fobj):
        self.write_head(fobj)
        a = self._atom
        a.seek_to_start()
        a.skip(a.head_size_ext())

        if a.v == 0:
            fobj.write(a.read_bytes(8))
            write_ulong(fobj, self.timescale)
            write_ulong(fobj, self.duration)
            a.skip(8)
        elif a.v == 1:
            fobj.write(a.read_bytes(16))
            write_ulong(fobj, self.timescale)
            write_ulonglong(fobj, self.duration)
            a.skip(12)
        else:
            raise RuntimeError()

        fobj.write(a.read_bytes(4))

class stts(FullBox):
    _fields = ('table',)

    @classmethod
    @fullboxread
    def read(cls, a):
        entries = read_ulong(a.f)
        t = read_table(a.f, 'LL', entries)
        return cls(a, table=t)

    def get_size(self):
        return self.tabled_size(4, 8)

    def write(self, fobj):
        self.write_head(fobj)
        write_ulong(fobj, len(self.table))
        for elt in self.table:
            write_ulong(fobj, elt[0])
            write_ulong(fobj, elt[1])

class ctts(FullBox):
    _fields = ('table',)

    @classmethod
    @fullboxread
    def read(cls, a):
        entries = read_ulong(a.f)
        t = read_table(a.f, 'LL', entries)
        return cls(a, table=t)

    def get_size(self):
        return self.tabled_size(4, 8)

    def write(self, fobj):
        self.write_head(fobj)
        write_ulong(fobj, len(self.table))
        for elt in self.table:
            write_ulong(fobj, elt[0])
            write_ulong(fobj, elt[1])

class stss(FullBox):
    _fields = ('table',)

    @classmethod
    @fullboxread
    def read(cls, a):
        entries = read_ulong(a.f)
        t = read_table(a.f, 'L', entries)
        return cls(a, table=t)

    def get_size(self):
        return self.tabled_size(4, 4)

    def write(self, fobj):
        self.write_head(fobj)
        write_ulong(fobj, len(self.table))
        for elt in self.table:
            write_ulong(fobj, elt)

class stsz(FullBox):
    _fields = ('sample_size', 'table')

    @classmethod
    @fullboxread
    def read(cls, a):
        ss = read_ulong(a.f)
        entries = read_ulong(a.f)
        if ss == 0:
            t = read_table(a.f, 'L', entries)
        else:
            t = []
        return cls(a, sample_size=ss, table=t)

    def get_size(self):
        if self.sample_size != 0:
            return self._atom.head_size_ext() + 8
        return self.tabled_size(8, 4)

    def write(self, fobj):
        self.write_head(fobj)
        write_ulong(fobj, self.sample_size)
        write_ulong(fobj, len(self.table))
        if self.sample_size == 0:
            for elt in self.table:
                write_ulong(fobj, elt)

class stsc(FullBox):
    _fields = ('table',)

    @classmethod
    @fullboxread
    def read(cls, a):
        entries = read_ulong(a.f)
        t = read_table(a.f, 'LLL', entries)
        return cls(a, table=t)

    def get_size(self):
        return self.tabled_size(4, 12)

    def write(self, fobj):
        self.write_head(fobj)
        write_ulong(fobj, len(self.table))
        for elt in self.table:
            write_ulong(fobj, elt[0])
            write_ulong(fobj, elt[1])
            write_ulong(fobj, elt[2])

class stco(FullBox):
    _fields = ('table',)

    @classmethod
    @fullboxread
    def read(cls, a):
        entries = read_ulong(a.f)
        t = read_table(a.f, 'L', entries)
        return cls(a, table=t)

    def get_size(self):
        return self.tabled_size(4, 4)

    def write(self, fobj):
        self.write_head(fobj)
        write_ulong(fobj, len(self.table))
        for elt in self.table:
            write_ulong(fobj, elt)

class co64(FullBox):
    _fields = ('table',)

    @classmethod
    @fullboxread
    def read(cls, a):
        entries = read_ulong(a.f)
        t = read_table(a.f, 'Q', entries)
        return cls(a, table=t)

    def get_size(self):
        return self.tabled_size(4, 8)

    def write(self, fobj):
        self.write_head(fobj)
        write_ulong(fobj, len(self.table))
        for elt in self.table:
            write_ulonglong(fobj, elt)

class stz2(FullBox):
    _fields = ('field_size', 'table')

    @classmethod
    @fullboxread
    def read(cls, a):
        field_size = read_ulong(a.f) & 0xff
        entries = read_ulong(a.f)

        def read_2u4(f):
            b = read_bytes(f, 1)
            return (b >> 4) & 0x0f, b & 0x0f
        def flatten(l):
            ret = []
            for elt in l:
                ret.extend(elt)
            return ret
        if field_size == 16:
            t = read_table(a.f, 'H', entries)
        elif field_size == 8:
            t = read_table(a.f, 'B', entries)
        elif field_size == 4:
            t = flatten([read_2u4(a.f) for _ in xrange((entries + 1) / 2)])
        else:
            raise FormatError()
        return cls(a, field_size=field_size, table=t)

    def get_size(self):
        fs = self.field_size / 8.0
        return int(self.tabled_size(8, fs))

    def write(self, fobj):
        self.write_head(fobj)
        write_ulong(fobj, self.field_size & 0xff)
        write_ulong(fobj, len(self.table))

        def write_u16(f, n):
            fobj.write(struct.pack('>H', n))
        def write_u8(f, n):
            fobj.write(struct.pack('B', n))
        def write_2u4(f, n, m):
            fobj.write(struct.pack('B', ((n & 0x0f) << 4) | (m & 0x0f)))
        if field_size == 16:
            for elt in self.table:
                write_u16(fobj, elt)
        elif field_size == 8:
            for elt in self.table:
                write_u8(fobj, elt)
        elif field_size == 4:
            for elt in takeby(self.table, 2):
                write_2u4(fobj, *elt)
        else:
            raise FormatError()

class btrt(Box):
    _fields = ('bufferSize', 'maxBitrate', 'avgBitrate')

    @classmethod
    def read(cls, a):
        a.seek_to_data()
        bufferSize = atoms.read_ulong(a.f)
        maxBitrate = atoms.read_ulong(a.f)
        avgBitrate = atoms.read_ulong(a.f)
        return cls(a, bufferSize=bufferSize, maxBitrate=maxBitrate,
                   avgBitrate=avgBitrate)

# from gst and mp4split, which all seem to be from ffmpeg
def read_desc_len(f):
    bytes = 0
    len = 0
    while True:
        c = atoms.read_uchar(f)
        len <<= 7
        len |= c & 0x7f
        bytes += 1
        if (bytes == 4):
            break
        if not (c & 0x80):
            break
    return len

MP4_ELEMENTARY_STREAM_DESCRIPTOR_TAG = 3
MP4_DECODER_CONFIG_DESCRIPTOR_TAG = 4
MP4_DECODER_SPECIFIC_DESCRIPTOR_TAG = 5

class esds(FullBox):
    _fields = ('object_type_id', 'maxBitrate', 'avgBitrate', 'data')

    @classmethod
    @fullboxread
    def read(cls, a):
        # from mp4split
        esdesc = atoms.read_uchar(a.f)
        if esdesc == MP4_ELEMENTARY_STREAM_DESCRIPTOR_TAG:
            len = read_desc_len(a.f)
            stream_id = atoms.read_ushort(a.f)
            prio = atoms.read_uchar(a.f)
        else:
            stream_id = atoms.read_ushort(a.f)

        tag = atoms.read_uchar(a.f)
        len = read_desc_len(a.f)
        if tag != MP4_DECODER_CONFIG_DESCRIPTOR_TAG:
            raise FormatError("can't parse esds")
        object_type_id = atoms.read_uchar(a.f)
        stream_type = atoms.read_uchar(a.f)
        buffer_size_db = a.read_bytes(3)
        maxBitrate = atoms.read_ulong(a.f)
        avgBitrate = atoms.read_ulong(a.f)

        tag = atoms.read_uchar(a.f)
        len = read_desc_len(a.f)
        if tag != MP4_DECODER_SPECIFIC_DESCRIPTOR_TAG:
            raise FormatError("can't parse esd")
        data = a.read_bytes(len)

        return cls(a, object_type_id=object_type_id,
                   maxBitrate=maxBitrate, avgBitrate=avgBitrate, data=data)

class mp4a(Box):
    # TODO: base class for SampleEntry, AudioSampleEntry...
    _fields = ('index', 'channelcount', 'samplesize', 'sampleratehi', 'sampleratelo', 'extra')

    @classmethod
    def read(cls, a):
        a.seek_to_data()
        a.skip(6) # reserved
        idx = atoms.read_ushort(a.f)
        version = atoms.read_ushort(a.f)
        a.skip(4 + 2) # reserved
        channelcount = atoms.read_ushort(a.f)
        if channelcount == 3:
            channelcount = 6 # from mp4split
        samplesize = atoms.read_ushort(a.f)
        a.skip(4)
        sampleratehi = atoms.read_ushort(a.f)
        sampleratelo = atoms.read_ushort(a.f)
        # FIXME: parse version != 0 samples_per_packet etc..
        # optional boxes follow
        extra = list(atoms.read_atoms(a.f, a.size - 36))
        a.seek_to_data()
        a.skip(36)
        extra = map(lambda a: maybe_build_atoms(a.type, [a])[0], extra)
        return cls(a, index=idx, channelcount=channelcount, samplesize=samplesize,
                   sampleratehi=sampleratehi, sampleratelo=sampleratelo,
                   extra=extra)

class avcC(Box):
    _fields = ('version', 'profile', 'level', 'data')

    @classmethod
    def read(cls, a):
        a.seek_to_data()
        data = a.read_bytes(a.size - 8)
        version = data[0]
        profile = data[1]
        level = data[3]
        return cls(a, version=version, profile=profile, level=level, data=data)

class avc1(Box):
    # TODO: base class for SampleEntry, VideoSampleEntry...
    _fields = ('index', 'width', 'height', 'comp', 'extra')

    @classmethod
    def read(cls, a):
        a.seek_to_data()
        a.skip(6)
        idx = atoms.read_ushort(a.f)
        a.skip(4 * 4)
        width = atoms.read_ushort(a.f)
        height = atoms.read_ushort(a.f)
        hr = a.read_bytes(4)
        vr = a.read_bytes(4)
        reserved = atoms.read_ulong(a.f)
        fc = atoms.read_ushort(a.f)
        comp = a.read_bytes(32)
        depth = atoms.read_ushort(a.f)
        minusone = atoms.read_short(a.f)
        if (minusone != -1):
            raise FormatError()
        # optional boxes follow
        extra = list(atoms.read_atoms(a.f, a.size - 86))
        a.seek_to_data()
        a.skip(86)
        extra = map(lambda a: maybe_build_atoms(a.type, [a])[0], extra)
        return cls(a, index=idx, width=width, height=height, comp=comp, extra=extra)

class stsd(FullBox):
    _fields = ('count','entries')

    @classmethod
    @fullboxread
    def read(cls, a):
        count = read_ulong(a.f)
        entries = []
        while count > 0:
            b = atoms.read_atom(a.f)
            entries.append(b)
            count = count - 1
        entries = map(lambda a: maybe_build_atoms(a.type, [a])[0], entries)
        return cls(a, count=count, entries=entries)

class stbl(ContainerBox):
    _fields = ('stss', 'stsz', 'stz2', 'stco', 'co64', 'stts', 'ctts', 'stsc', 'stsd')

    @classmethod
    @containerboxread
    def read(cls, a):
        (astss, astsz, astz2, astco, aco64, astts, actts, astsc, stsd) = \
            select_children_atoms(a, ('stss', 0, 1), ('stsz', 0, 1),
                                  ('stz2', 0, 1), ('stco', 0, 1),
                                  ('co64', 0, 1), ('stts', 1, 1),
                                  ('ctts', 0, 1), ('stsc', 1, 1),
                                  ('stsd', 0, 1))
        return cls(a, stss=astss, stsz=astsz, stz2=astz2, stco=astco,
                   co64=aco64, stts=astts, ctts=actts, stsc=astsc,
                   stsd=stsd)

class minf(ContainerBox):
    _fields = ('stbl',)

    @classmethod
    @containerboxread
    def read(cls, a):
        (astbl,) = select_children_atoms(a, ('stbl', 1, 1))
        return cls(a, stbl=astbl)

class mdia(ContainerBox):
    _fields = ('mdhd', 'minf')

    @classmethod
    @containerboxread
    def read(cls, a):
        (amdhd, aminf) = select_children_atoms(a, ('mdhd', 1, 1),
                                               ('minf', 1, 1))
        return cls(a, mdhd=amdhd, minf=aminf)

class trak(ContainerBox):
    _fields = ('tkhd', 'mdia')

    @classmethod
    @containerboxread
    def read(cls, a):
        (atkhd, amdia) = select_children_atoms(a, ('tkhd', 1, 1),
                                               ('mdia', 1, 1))
        return cls(a, tkhd=atkhd, mdia=amdia)

class moov(ContainerBox):
    _fields = ('mvhd', 'trak')

    @classmethod
    @containerboxread
    def read(cls, a):
        (amvhd, traks) = select_children_atoms(a, ('mvhd', 1, 1),
                                               ('trak', 1, None))
        return cls(a, mvhd=amvhd, trak=traks)

class ftyp(Box):
    _fields = ('brand', 'version')

    @classmethod
    def read(cls, a):
        a.seek_to_data()
        brand = read_fcc(a.f)
        v = read_ulong(a.f)
        return cls(a, brand=brand, version=v)

class tfhd(FullBox):
    _fields = ('track_id', )

    @classmethod
    @fullboxread
    def read(cls, a):
        track_id = read_ulong(a.f)
        return cls(a, track_id=track_id)

class traf(ContainerBox):
    _fields = ('tfhd', 'trun', 'sdtp', 'uuid')

    @classmethod
    @containerboxread
    def read(cls, a):
        (tfhd, trun, sdtp) = select_children_atoms(a, ('tfhd', 1, 1),
                                                   ('trun', 1, 1),
                                                   ('sdtp', 0, 1))
        uuid = []
        return cls(a, tfhd=tfhd, trun=trun, sdtp=sdtp, uuid=uuid)

class moof(ContainerBox):
    _fields = ('mfhd', 'traf')

    @classmethod
    @containerboxread
    def read(cls, a):
        (mfhd, traf) = select_children_atoms(a, ('mfhd', 1, 1),
                                             ('traf', 1, 1))
        return cls(a, mfhd=mfhd, traf=traf)

def read_iso_file(fobj):
    fobj.seek(0)

    al = list(atoms.read_atoms(fobj))
    ad = atoms.atoms_dict(al)
    aftyp, amoov, mdat = select_atoms(ad, ('ftyp', 1, 1), ('moov', 1, 1),
                                      ('mdat', 1, None))
    # print '(first mdat offset: %d)' % mdat[0].offset

    return aftyp, amoov, al

def find_cut_trak_info(atrak, t):
    ts = atrak.mdia.mdhd.timescale
    stbl = atrak.mdia.minf.stbl
    mt = int(round(t * ts))
    # print 'media time:', mt, t, ts, t * ts
    # print ('finding cut for trak %r @ time %r (%r/%r)' %
    #        (atrak._atom, t, mt, ts))
    sample = find_samplenum_stts(stbl.stts.table, mt)
    chunk = find_chunknum_stsc(stbl.stsc.table, sample)
    # print ('found sample: %d and chunk: %d/%r' %
    #        (sample, chunk, stbl.stsc.table[-1]))
    stco64 = stbl.stco or stbl.co64
    chunk_offset = get_chunk_offset(stco64.table, chunk)
    zero_offset = get_chunk_offset(stco64.table, 1)
    # print 'found chunk offsets:', chunk_offset, zero_offset
    return sample, chunk, zero_offset, chunk_offset

def cut_stco64(stco64, chunk_num, offset_change, first_chunk_delta=0):
    new_table = [offset - offset_change for offset in stco64[chunk_num - 1:]]
    if new_table and first_chunk_delta:
        new_table[0] = new_table[0] + first_chunk_delta
    return new_table

def cut_stco64_stsc(stco64, stsc, stsz2, chunk_num, sample_num, offset_change):
    new_stsc = None

    i, n = 0, len(stsc)
    current, per_chunk, sdidx = 1, 0, None
    samples = 1
    while i < n:
        next, next_per_chunk, next_sdidx = stsc[i]
        if next > chunk_num:
            offset = chunk_num - 1
            new_stsc = ([(1, per_chunk, sdidx)]
                        + [(c - offset, p_c, j)
                           for (c, p_c, j) in stsc[i:]])
            break
        samples += (next - current) * per_chunk
        current, per_chunk, sdidx = next, next_per_chunk, next_sdidx
        i += 1
    if new_stsc is None:
        new_stsc = [(1, per_chunk, sdidx)]

    lead_samples = (sample_num - samples) % per_chunk

    bytes_offset = 0
    if lead_samples > 0:
        bytes_offset = sum(stsz2[sample_num - 1 - lead_samples :
                                     sample_num - 1])
    # print 'lead_samples:', lead_samples, 'bytes_offset:', bytes_offset

    if lead_samples > 0:
        fstsc = new_stsc[0]
        new_fstsc = (1, fstsc[1] - lead_samples, fstsc[2])
        # print 'old stsc', new_stsc
        if len(new_stsc) > 1 and new_stsc[1][0] == 2:
            new_stsc[0] = new_fstsc
        else:
            new_stsc[0:1] = [new_fstsc, (2, fstsc[1], fstsc[2])]
        # print 'new stsc', new_stsc

    return (cut_stco64(stco64, chunk_num, offset_change, bytes_offset),
            new_stsc)

def cut_sctts(sctts, sample):
    samples = 1
    i, n = 0, len(sctts)
    while i < n:
        count, delta = sctts[i]
        if samples + count > sample:
            return [(samples + count - sample, delta)] + sctts[i+1:]
        samples += count
        i += 1
    return []                   # ? :/

def cut_stss(stss, sample):
    i, n = 0, len(stss)
    while i < n:
        snum = stss[i]
        # print 'cut_stss:', snum, sample
        if snum >= sample:
            return [s - sample + 1 for s in stss[i:]]
        i += 1
    return []

def cut_stsz2(stsz2, sample):
    if not stsz2:
        return []
    return stsz2[sample - 1:]

def cut_trak(atrak, sample, data_offset_change):
    stbl = atrak.mdia.minf.stbl
    chunk = find_chunknum_stsc(stbl.stsc.table, sample)
    # print ('cutting trak: %r @ sample %d [chnk %d]' %
    #        (atrak._atom, sample, chunk))
    media_time_diff = find_mediatime_stts(stbl.stts.table, sample) # - 0
    new_media_duration = atrak.mdia.mdhd.duration - media_time_diff

    
    """
    cut_stco64()
    cut_stsc()
    cut_stsz2()
    cut_sctts(stts)
    cut_sctts(ctts)
    cut_stss()
    """
    
    stco64 = stbl.stco or stbl.co64
    stsz2 = stbl.stsz or stbl.stz2

    new_stco64_t, new_stsc_t = cut_stco64_stsc(stco64.table, stbl.stsc.table,
                                               stsz2.table, chunk, sample,
                                               data_offset_change)

    new_stco64 = stco64.copy(table=new_stco64_t)

    new_stsc = stbl.stsc.copy(table=new_stsc_t)

    new_stsz2 = stsz2.copy(table=cut_stsz2(stsz2.table, sample))

    new_stts = stbl.stts.copy(table=cut_sctts(stbl.stts.table, sample))

    new_ctts = None
    if stbl.ctts:
        new_ctts = stbl.ctts.copy(table=cut_sctts(stbl.ctts.table, sample))

    new_stss = None
    if stbl.stss:
        new_stss = stbl.stss.copy(table=cut_stss(stbl.stss.table, sample))

    """
    new_mdhd = atrak.mdia.mdhd.copy()
    new_minf = atrak.mdia.minf.copy()
    new_mdia = atrak.mdia.copy()
    new_trak = atrak.copy()
    """

    stbl_attribs = dict(stts=new_stts, stsc=new_stsc)
    stbl_attribs[stbl.stco and 'stco' or 'co64'] = new_stco64
    stbl_attribs[stbl.stsz and 'stsz' or 'stz2'] = new_stsz2
    if new_ctts:
        stbl_attribs['ctts'] = new_ctts
    if new_stss:
        stbl_attribs['stss'] = new_stss

    new_stbl = stbl.copy(**stbl_attribs)
    new_minf = atrak.mdia.minf.copy(stbl=new_stbl)
    new_mdhd = atrak.mdia.mdhd.copy(duration=new_media_duration)
    new_mdia = atrak.mdia.copy(mdhd=new_mdhd, minf=new_minf)
    new_tkhd = atrak.tkhd.copy()
    new_trak = atrak.copy(tkhd=new_tkhd, mdia=new_mdia)

    # print 'old trak:'
    # print atrak

    return new_trak

def update_offsets(atrak, data_offset_change):
    """
    cut_stco64(stco64, 1, ...)  # again, after calculating new size of moov
    atrak.mdia.mdhd.duration = new_duration
    """

    # print 'offset updates:'
    # print atrak

    stbl = atrak.mdia.minf.stbl
    stco64 = stbl.stco or stbl.co64
    stco64.table = cut_stco64(stco64.table, 1, data_offset_change)

    # print atrak
    # print

def cut_moov(amoov, t):
    ts = amoov.mvhd.timescale
    duration = amoov.mvhd.duration
    if t * ts >= duration:
        raise RuntimeError('Exceeded file duration: %r' %
                           (duration / float(ts)))
    traks = amoov.trak
    # print 'movie timescale: %d, num tracks: %d' % (ts, len(traks))
    # print
    cut_info = map(lambda a: find_cut_trak_info(a, t), traks)
    # print 'cut_info:', cut_info
    new_data_offset = min([ci[3] for ci in cut_info])
    zero_offset = min([ci[2] for ci in cut_info])
    # print 'new offset: %d, delta: %d' % (new_data_offset,
    #                                      new_data_offset - zero_offset)

    new_traks = map(lambda a, ci: cut_trak(a, ci[0],
                                           new_data_offset - zero_offset),
                    traks, cut_info)

    new_moov = amoov.copy(mvhd=amoov.mvhd.copy(), trak=new_traks)

    moov_size_diff = amoov.get_size() - new_moov.get_size()
    # print ('moov_size_diff', moov_size_diff, amoov.get_size(),
    #        new_moov.get_size())
    # print 'real moov sizes', amoov._atom.size, new_moov._atom.size
    # print 'new mdat start', zero_offset - moov_size_diff - 8

    def update_trak_duration(atrak):
        amdhd = atrak.mdia.mdhd
        new_duration = amdhd.duration * ts // amdhd.timescale # ... different
                                                                # rounding? :/
        atrak.tkhd.duration = new_duration

    # print

    map(update_trak_duration, new_traks)
    map(lambda a: update_offsets(a, moov_size_diff), new_traks)

    return new_moov, new_data_offset - zero_offset, new_data_offset


def split_atoms(f, out_f, t):
    aftype, amoov, alist = read_iso_file(f)
    t = find_nearest_syncpoint(amoov, t)
    # print 'nearest syncpoint:', t
    nmoov, delta, new_offset = cut_moov(amoov, t)

    write_split_header(out_f, nmoov, alist, delta)

    return new_offset

def update_mdat_atoms(alist, size_delta):
    updated = []
    to_remove = size_delta
    pos = alist[0].offset
    for a in alist:
        data_size = a.size - a.head_size()
        size_change = min(data_size, to_remove)
        if size_change > 0:
            to_remove -= size_change
        new_size = real_size = a.size - size_change
        if a.real_size == 1:
            real_size = 1
        updated.append(atoms.Atom(new_size, 'mdat', pos, a.f,
                                  real_size=real_size))
        if to_remove == 0:
            break
        pos += new_size
    return updated

def write_split_header(out_f, amoov, alist, size_delta):
    moov_idx = find_atom(alist, 'moov')
    mdat_idx = find_atom(alist, 'mdat')

    mdat = alist[mdat_idx]

    cut_offset = mdat.offset + mdat.head_size() + size_delta
    to_update = [a for a in alist[mdat_idx:] if a.offset < cut_offset]

    if [a for a in to_update if a.type != 'mdat']:
        raise FormatError('"mdat" and non-"mdat" (to-update) atoms mixed')

    updated_mdats = update_mdat_atoms(to_update, size_delta)

    alist[moov_idx] = amoov

    write_atoms(alist[:mdat_idx], out_f)

    for a in updated_mdats:
        write_ulong(out_f, a.real_size)
        write_fcc(out_f, a.type)
        if a.real_size == 1:
            write_ulonglong(out_f, a.size)

def split(f, t, out_f=None):
    wf = out_f
    if wf is None:
        from cStringIO import StringIO
        wf = StringIO()

    new_offset = split_atoms(f, wf, t)
    return wf, new_offset

def split_and_write(in_f, out_f, t):
    header_f, new_offset = split(in_f, t)
    header_f.seek(0)
    out_f.write(header_f.read())
    in_f.seek(new_offset)
    out_f.write(in_f.read())

def main(f, t):
    split_and_write(f, file('/tmp/t.mp4', 'w'), t)

def find_sync_points(amoov):
    ts = amoov.mvhd.timescale
    traks = amoov.trak
    def find_sync_samples(a):
        stbl = a.mdia.minf.stbl
        if not stbl.stss:
            return []
        stss = stbl.stss
        stts = stbl.stts.table
        ts = float(a.mdia.mdhd.timescale)
        return map(lambda mt: mt / ts, find_mediatimes(stts, stss.table))
    sync_tables = [t for t in map(find_sync_samples, traks) if t]
    if sync_tables:
        # ideally there should be only one sync table (from a video
        # trak) - an arbitrary one will be taken otherwise...
        return sync_tables[0]
    return []

def find_nearest_syncpoint(amoov, t):
    syncs = find_sync_points(amoov)

    if not syncs:
        # hardcoding duration - 0.1 sec as the farthest seek pos for now...
        max_ts = amoov.mvhd.duration / float(amoov.mvhd.timescale) - 0.1
        return max(0, min(t, max_ts))

    found = 0
    other = 0
    for ss in syncs:
        if ss > t:
            other = ss
            break
        found = ss
    if (abs(t - found) < abs(other - t)):
        return found
    return other

def get_nearest_syncpoint(f, t):
    aftyp, amoov, alist = read_iso_file(f)
    print find_nearest_syncpoint(amoov, t)

def get_sync_points(f):
    aftyp, amoov, alist = read_iso_file(f)
    return find_sync_points(amoov)

def get_debugging(f):
    aftyp, amoov, alist = read_iso_file(f)
    ts = amoov.mvhd.timescale
    print aftyp
    traks = amoov.trak

    from pprint import pprint
    pprint(map(lambda a: a.mdia.minf.stbl.stco, traks))

def change_chunk_offsets(amoov, data_offset):
    """
    @param data_offset: number of bytes to add to chunk offsets in all
                        traks of amoov
    @type  data_offset: int
    """
    # FIXME: make the offset direction sane in update_offsets...?
    map(lambda a: update_offsets(a, - data_offset), amoov.trak)

def move_header_to_front(f):
    aftype, amoov, alist = read_iso_file(f)

    moov_idx = find_atom(alist, 'moov')
    mdat_idx = find_atom(alist, 'mdat')

    if moov_idx < mdat_idx:
        # nothing to be done
        return None

    adict = atoms.atoms_dict(alist)
    mdat = alist[mdat_idx]

    new_moov_idx = mdat_idx
    if 'wide' in adict:
        # if 'wide' atom preceeds 'mdat', let's keep it that way
        for wide in adict['wide']:
            if wide.offset + wide.size == mdat.offset:
                new_moov_idx -= 1
                break

    # for the moment assuming rewriting offsets in moov won't change
    # the atoms sizes - could happen if:
    #   2**32 - 1 - last_chunk_offset < moov.size
    data_offset = amoov.get_size()

    change_chunk_offsets(amoov, data_offset)

    del alist[moov_idx]
    alist[new_moov_idx:new_moov_idx] = [amoov]

    return alist

def move_header_and_write(in_f, out_f):
    alist = move_header_to_front(in_f)
    if alist:
        write_atoms(alist, out_f)
        return True
    return False


if __name__ == '__main__':
    import sys
    f = file(sys.argv[1])
    if len(sys.argv) > 2:
        t = float(sys.argv[2])
        main(f, t)
        # get_nearest_syncpoint(f, t)
    else:
        print get_sync_points(f)
        # get_debugging(f)
