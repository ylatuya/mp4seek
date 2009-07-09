import struct

import atoms
from atoms import read_fcc, read_ulong, read_ulonglong


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

class FullBox(Box):
    pass

class ContainerBox(Box):
    pass

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
                               ' found: %d' %
                               (atype, req_min, req_max, found))
        alist = maybe_build_atoms(atype, alist)
        if req_max == 1:
            if found == 0:
                selected.append(None)
            else:
                selected.append(alist[0])
        else:
            selected.append(alist)
    return selected

def ellipsisize(l, num=4):
    if len(l) <= num:
        return l
    # ... for displaying, "ellipsisize!" :P
    return l[0:min(num, len(l) - 1)] + ['...'] + l[-1:]

def container_children(a):
    a = atoms.container(a)
    cd = atoms.atoms_dict(a.read_children())
    return a, cd

def find_cut_stts(stts, mt):
    "stts - table of the 'stts' atom; mt - media time"
    current = 0
    trimmed = None
    i, n = 0, len(stts)
    while i < n:
        count, delta = stts[i]
        cdelta = count * delta
        if mt == current:
            trimmed = stts[i + 1:]
            break
        elif mt < current + cdelta:
            new_count = count - (mt - current) / delta
            trimmed = [(new_count, delta)] + stts[i + 1:]
            break
        current += cdelta
        i += 1
    return trimmed or stts

def find_samplenum_stts(stts, mt):
    "stts - table of the 'stts' atom; mt - media time"
    ctime = 0
    samples = 1
    i, n = 0, len(stts)
    while i < n:
        if mt == ctime:
            break
        count, delta = stts[i]
        cdelta = count * delta
        if mt < ctime + cdelta:
            samples += (mt - ctime) // delta
            break
        ctime += cdelta
        samples += count
        i += 1

    return samples

def find_chunknum_stsc(stsc, sample_num):
    current = 1                 # 1-based indices!
    per_chunk = 0
    samples = 1
    i, n = 0, len(stsc)
    while i < n:
        next, next_per_chunk, _sdidx = stsc[i]
        samples_here = (next - current) * per_chunk
        if samples + samples_here > sample_num:
            break
        samples += samples_here
        current, per_chunk = next, next_per_chunk
        i += 1
    return (sample_num - samples) // per_chunk + current

def get_chunk_offset(stco64, chunk_num):
    # 1-based indices!
    return stco64[chunk_num - 1]

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

class tkhd(FullBox):
    _fields = ('duration',)

    @classmethod
    @fullboxread
    def read(cls, a):
        ver_skip(a, (16, 24))
        d = ver_read(a, (read_ulong, read_ulonglong))
        return cls(a, duration=d)

class mdhd(FullBox):
    _fields = ('timescale', 'duration')

    @classmethod
    @fullboxread
    def read(cls, a):
        ver_skip(a, (8, 16))
        ts = read_ulong(a.f)
        d = ver_read(a, (read_ulong, read_ulonglong))
        return cls(a, timescale=ts, duration=d)

class stts(FullBox):
    _fields = ('table',)

    @classmethod
    @fullboxread
    def read(cls, a):
        entries = read_ulong(a.f)
        t = [(read_ulong(a.f), read_ulong(a.f)) for _ in xrange(entries)]
        return cls(a, table=t)

class ctts(FullBox):
    _fields = ('table',)

    @classmethod
    @fullboxread
    def read(cls, a):
        entries = read_ulong(a.f)
        t = [(read_ulong(a.f), read_ulong(a.f)) for _ in xrange(entries)]
        return cls(a, table=t)

class stss(FullBox):
    _fields = ('table',)

    @classmethod
    @fullboxread
    def read(cls, a):
        entries = read_ulong(a.f)
        t = [read_ulong(a.f) for _ in xrange(entries)]
        return cls(a, table=t)

class stsz(FullBox):
    _fields = ('sample_size', 'table')

    @classmethod
    @fullboxread
    def read(cls, a):
        ss = read_ulong(a.f)
        entries = read_ulong(a.f)
        if ss == 0:
            t = [read_ulong(a.f) for _ in xrange(entries)]
        else:
            t = []
        return cls(a, sample_size=ss, table=t)

class stsc(FullBox):
    _fields = ('table',)

    @classmethod
    @fullboxread
    def read(cls, a):
        entries = read_ulong(a.f)
        t = [(read_ulong(a.f), read_ulong(a.f), read_ulong(a.f))
             for _ in xrange(entries)]
        return cls(a, table=t)

class stco(FullBox):
    _fields = ('table',)

    @classmethod
    @fullboxread
    def read(cls, a):
        entries = read_ulong(a.f)
        t = [read_ulong(a.f) for _ in xrange(entries)]
        return cls(a, table=t)

class co64(FullBox):
    _fields = ('table',)

    @classmethod
    @fullboxread
    def read(cls, a):
        entries = read_ulong(a.f)
        t = [read_ulonglong(a.f) for _ in xrange(entries)]
        return cls(a, table=t)

class stz2(FullBox):
    _fields = ('field_size', 'table')

    @classmethod
    @fullboxread
    def read(cls, a):
        field_size = read_ulong(a.f) & 0xff
        entries = read_ulong(a.f)

        def read_u16(f):
            return struct.unpack('>H', read_bytes(f, 2))[0]
        def read_u8(f):
            return read_bytes(f, 1)
        def read_2u4(f):
            b = read_bytes(f, 1)
            return (b >> 4) & 0x0f, b & 0x0f
        def flatten(l):
            ret = []
            for elt in l:
                ret.extend(elt)
            return ret
        if field_size == 16:
            t = [read_u16(a.f) for _ in xrange(entries)]
        elif field_size == 8:
            t = [read_u8(a.f) for _ in xrange(entries)]
        elif field_size == 4:
            t = flatten([read_2u4(a.f) for _ in xrange((entries + 1) / 2)])
        else:
            raise FormatError()
        return cls(a, field_size=field_size, table=t)

class stbl(ContainerBox):
    _fields = ('stss', 'stsz', 'stz2', 'stco', 'co64', 'stts', 'ctts', 'stsc')

    @classmethod
    @containerboxread
    def read(cls, a):
        (astss, astsz, astz2, astco, aco64, astts, actts, astsc) = \
            select_children_atoms(a, ('stss', 0, 1), ('stsz', 0, 1),
                                  ('stz2', 0, 1), ('stco', 0, 1),
                                  ('co64', 0, 1), ('stts', 1, 1),
                                  ('ctts', 0, 1), ('stsc', 1, 1))
        return cls(a, stss=astss, stsz=astsz, stz2=astz2, stco=astco,
                   co64=aco64, stts=astts, ctts=actts, stsc=astsc)

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

def read_iso_file(fobj):
    fobj.seek(0)

    ad = atoms.atoms_dict(atoms.read_atoms(fobj))
    aftyp, amoov, mdat = select_atoms(ad, ('ftyp', 1, 1), ('moov', 1, 1),
                                      ('mdat', 1, None))
    print '(first mdat offset: %d)' % mdat[0].offset

    return aftyp, amoov

def find_cut_trak_info(atrak, t):
    ts = atrak.mdia.mdhd.timescale
    stbl = atrak.mdia.minf.stbl
    mt = int(t * ts)
    sample = find_samplenum_stts(stbl.stts.table, mt)
    chunk = find_chunknum_stsc(stbl.stsc.table, sample)
    stco64 = stbl.stco or stbl.co64
    chunk_offset = get_chunk_offset(stco64.table, chunk)
    zero_offset = get_chunk_offset(stco64.table, 1)
    return sample, chunk, zero_offset, chunk_offset

def cut_stco64(stco64, chunk_num, offset_change):
    new_table = [offset - offset_change for offset in stco64[chunk_num - 1:]]
    return new_table

def cut_stsc(stsc, chunk_num):
    i, n = 0, len(stsc)
    current, per_chunk, sdidx = None, None, None
    while i < n:
        next, next_per_chunk, next_sdidx = stsc[i]
        if next > chunk_num:
            return [(chunk_num, per_chunk, sdidx)] + stsc[i:]
        current, per_chunk, sdidx = next, next_per_chunk, next_sdidx
        i += 1
    return [(chunk_num, per_chunk, sdidx)]

def cut_sctts(sctts, sample):
    samples = 1
    i, n = 0, len(stts)
    while i < n:
        count, delta = stts[i]
        if samples + count > sample:
            return [(samples + count - sample, delta)] + stts[i+1:]
        samples += count
        i += 1
    return []                   # ? :/

def cut_stss(stss, sample):
    i, n = 0, len(stss)
    while i < n:
        snum = stss[i]
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
    
    """
    cut_stco64()
    cut_stsc()
    cut_stsz2()
    cut_sctts(stts)
    cut_sctts(ctts)
    cut_stss()
    cut_stco64(stco64, 1, ...)  # again, after calculating new size of moov
    atrak.mdia.mdhd.duration = new_duration
    """
    

def cut_moov(amoov, t):
    ts = amoov.mvhd.timescale
    duration = amoov.mvhd.duration
    if t * ts >= duration:
        raise RuntimeError('Exceeded file duration: %r' %
                           (duration / float(ts)))
    traks = amoov.trak
    print 'movie timescale: %d, num tracks: %d' % (ts, len(traks))
    print
    cut_info = map(lambda a: find_cut_trak_info(a, t), traks)
    print cut_info
    new_data_offset = min([ci[3] for ci in cut_info])
    zero_offset = min([ci[2] for ci in cut_info])
    print 'new offset: %d, delta: %d' % (new_data_offset,
                                         new_data_offset - zero_offset)

if __name__ == '__main__':
    import sys
    f = file(sys.argv[1])
    from pprint import pprint
    iso = read_iso_file(f)
    print iso[0]
    amoov = iso[1]
    cut_moov(amoov, float(sys.argv[2]))