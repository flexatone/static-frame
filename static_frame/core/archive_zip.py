"""
Optimized reader of ZIP files. Based largely on CPython, Lib/zipfile/__init__.py

"""
import binascii
import io
import os
import shutil
import stat
import struct
import sys
import threading
import time
import typing as tp
# from zipfile import ZIP_BZIP2
# from zipfile import ZIP_DEFLATED
# from zipfile import ZIP_LZMA
from zipfile import ZIP_STORED
from zipfile import BadZipFile
from zipfile import LargeZipFile

# constants for Zip file compression methods
# ZIP_STORED = 0
# ZIP_DEFLATED = 8
# ZIP_BZIP2 = 12
# ZIP_LZMA = 14

try:
    import zlib  # We may need its compression method
    crc32 = zlib.crc32
except ImportError:
    zlib = None
    crc32 = binascii.crc32

# try:
#     import bz2  # We may need its compression method
# except ImportError:
#     bz2 = None

# try:
#     import lzma  # We may need its compression method
# except ImportError:
#     lzma = None


ZIP64_LIMIT = (1 << 31) - 1
ZIP_FILECOUNT_LIMIT = (1 << 16) - 1
ZIP_MAX_COMMENT = (1 << 16) - 1

# Other ZIP compression methods not supported

# DEFAULT_VERSION = 20
# ZIP64_VERSION = 45
# BZIP2_VERSION = 46
# LZMA_VERSION = 63
# we recognize (but not necessarily support) all features up to that version
# MAX_EXTRACT_VERSION = 63

# Below are some formats and associated data for reading/writing headers using
# the struct module.  The names and structures of headers/records are those used
# in the PKWARE description of the ZIP file format:
#     http://www.pkware.com/documents/casestudies/APPNOTE.TXT
# (URL valid as of January 2008)

# The "end of central directory" structure, magic number, size, and indices
# (section V.I in the format document)
_END_ARCHIVE_STRUCT = b"<4s4H2LH"
_END_ARCHIVE_STRING = b"PK\005\006"
_END_ARCHIVE_SIZE = struct.calcsize(_END_ARCHIVE_STRUCT)

_ECD_SIGNATURE = 0
_ECD_DISK_NUMBER = 1
_ECD_DISK_START = 2
_ECD_ENTRIES_THIS_DISK = 3
_ECD_ENTRIES_TOTAL = 4
_ECD_SIZE = 5
_ECD_OFFSET = 6
_ECD_COMMENT_SIZE = 7
# These last two indices are not part of the structure as defined in the
# spec, but they are used internally by this module as a convenience
_ECD_COMMENT = 8
_ECD_LOCATION = 9

# The "central directory" structure, magic number, size, and indices
# of entries in the structure (section V.F in the format document)
_CENTRAL_DIR_STRUCT = "<4s4B4HL2L5H2L"
_CENTRAL_DIR_STRING = b"PK\001\002"
_CENTRAL_DIR_SIZE = struct.calcsize(_CENTRAL_DIR_STRUCT)

# indexes of entries in the central directory structure
_CD_SIGNATURE = 0
_CD_CREATE_VERSION = 1
_CD_CREATE_SYSTEM = 2
_CD_EXTRACT_VERSION = 3
_CD_EXTRACT_SYSTEM = 4
_CD_FLAG_BITS = 5
_CD_COMPRESS_TYPE = 6
_CD_TIME = 7
_CD_DATE = 8
_CD_CRC = 9
_CD_COMPRESSED_SIZE = 10
_CD_UNCOMPRESSED_SIZE = 11
_CD_FILENAME_LENGTH = 12
_CD_EXTRA_FIELD_LENGTH = 13
_CD_COMMENT_LENGTH = 14
_CD_DISK_NUMBER_START = 15
_CD_INTERNAL_FILE_ATTRIBUTES = 16
_CD_EXTERNAL_FILE_ATTRIBUTES = 17
_CD_LOCAL_HEADER_OFFSET = 18

# General purpose bit flags
# Zip Appnote: 4.4.4 general purpose bit flag: (2 bytes)
_MASK_ENCRYPTED = 1 << 0
# Bits 1 and 2 have different meanings depending on the compression used.
_MASK_COMPRESS_OPTION_1 = 1 << 1
# _MASK_COMPRESS_OPTION_2 = 1 << 2
# _MASK_USE_DATA_DESCRIPTOR: If set, crc-32, compressed size and uncompressed
# size are zero in the local header and the real values are written in the data
# descriptor immediately following the compressed data.
_MASK_USE_DATA_DESCRIPTOR = 1 << 3
# Bit 4: Reserved for use with compression method 8, for enhanced deflating.
# _MASK_RESERVED_BIT_4 = 1 << 4
_MASK_COMPRESSED_PATCH = 1 << 5
_MASK_STRONG_ENCRYPTION = 1 << 6
# _MASK_UNUSED_BIT_7 = 1 << 7
# _MASK_UNUSED_BIT_8 = 1 << 8
# _MASK_UNUSED_BIT_9 = 1 << 9
# _MASK_UNUSED_BIT_10 = 1 << 10
_MASK_UTF_FILENAME = 1 << 11
# Bit 12: Reserved by PKWARE for enhanced compression.
# _MASK_RESERVED_BIT_12 = 1 << 12
# _MASK_ENCRYPTED_CENTRAL_DIR = 1 << 13
# Bit 14, 15: Reserved by PKWARE
# _MASK_RESERVED_BIT_14 = 1 << 14
# _MASK_RESERVED_BIT_15 = 1 << 15

# The "local file header" structure, magic number, size, and indices
# (section V.A in the format document)
_FILE_HEADER_STRUCT = "<4s2B4HL2L2H"
_FILE_HEADER_STRING = b"PK\003\004"
_FILE_HEADER_SIZE = struct.calcsize(_FILE_HEADER_STRUCT)

_FH_SIGNATURE = 0
_FH_EXTRACT_VERSION = 1
_FH_EXTRACT_SYSTEM = 2
_FH_GENERAL_PURPOSE_FLAG_BITS = 3
_FH_COMPRESSION_METHOD = 4
_FH_LAST_MOD_TIME = 5
_FH_LAST_MOD_DATE = 6
_FH_CRC = 7
_FH_COMPRESSED_SIZE = 8
_FH_UNCOMPRESSED_SIZE = 9
_FH_FILENAME_LENGTH = 10
_FH_EXTRA_FIELD_LENGTH = 11

# The "Zip64 end of central directory locator" structure, magic number, and size
_END_ARCHIVE64_LOCATOR_STRUCT = "<4sLQL"
_END_ARCHIVE64_LOCATOR_STRING = b"PK\x06\x07"
_END_ARCHIVE64_LOCATOR_SIZE = struct.calcsize(_END_ARCHIVE64_LOCATOR_STRUCT)

# The "Zip64 end of central directory" record, magic number, size, and indices
# (section V.G in the format document)
_END_ARCHIVE64_STRUCT = "<4sQ2H2L4Q"
_END_ARCHIVE64_STRING = b"PK\x06\x06"
_END_ARCHIVE64_SIZE = struct.calcsize(_END_ARCHIVE64_STRUCT)

_CD64_SIGNATURE = 0
_CD64_DIRECTORY_RECSIZE = 1
_CD64_CREATE_VERSION = 2
_CD64_EXTRACT_VERSION = 3
_CD64_DISK_NUMBER = 4
_CD64_DISK_NUMBER_START = 5
_CD64_NUMBER_ENTRIES_THIS_DISK = 6
_CD64_NUMBER_ENTRIES_TOTAL = 7
_CD64_DIRECTORY_SIZE = 8
_CD64_OFFSET_START_CENTDIR = 9

_DD_SIGNATURE = 0x08074b50


# class _Extra(bytes):
#     FIELD_STRUCT = struct.Struct('<HH')

#     def __new__(cls, val, id=None):
#         return super().__new__(cls, val)

#     def __init__(self, val, id=None):
#         self.id = id

#     @classmethod
#     def read_one(cls, raw):
#         try:
#             xid, xlen = cls.FIELD_STRUCT.unpack(raw[:4])
#         except struct.error:
#             xid = None
#             xlen = 0
#         return cls(raw[:4+xlen], xid), raw[4+xlen:]

#     @classmethod
#     def split(cls, data):
#         # use memoryview for zero-copy slices
#         rest = memoryview(data)
#         while rest:
#             extra, rest = _Extra.read_one(rest)
#             yield extra

#     @classmethod
#     def strip(cls, data, xids):
#         """Remove Extra fields with specified IDs."""
#         return b''.join(
#             ex
#             for ex in cls.split(data)
#             if ex.id not in xids
#         )


# def _check_zipfile(fp):
#     try:
#         if _extract_end_archive(fp):
#             return True         # file has correct magic number
#     except OSError:
#         pass
#     return False

# def is_zipfile(filename):
#     """Quickly see if a file is a ZIP file by checking the magic number.

#     The filename argument may be a file or file-like object too.
#     """
#     result = False
#     try:
#         if hasattr(filename, "read"):
#             result = _check_zipfile(fp=filename)
#         else:
#             with open(filename, "rb") as fp:
#                 result = _check_zipfile(fp)
#     except OSError:
#         pass
#     return result

TEndArchive = tp.List[tp.Union[bytes, int]]

def _end_archive_update_zip64(fpin: tp.IO[bytes],
        offset: int,
        endrec: TEndArchive,
        ) -> TEndArchive:
    """
    Read the ZIP64 end-of-archive records and use that to update endrec
    """
    try:
        fpin.seek(offset - _END_ARCHIVE64_LOCATOR_SIZE, 2)
    except OSError:
        # If the seek fails, the file is not large enough to contain a ZIP64
        # end-of-archive record, so just return the end record we were given.
        return endrec

    data = fpin.read(_END_ARCHIVE64_LOCATOR_SIZE)
    if len(data) != _END_ARCHIVE64_LOCATOR_SIZE:
        return endrec

    sig, diskno, reloff, disks = struct.unpack(_END_ARCHIVE64_LOCATOR_STRUCT, data)
    if sig != _END_ARCHIVE64_LOCATOR_STRING:
        return endrec

    if diskno != 0 or disks > 1:
        raise BadZipFile("zipfiles that span multiple disks are not supported")

    # Assume no 'zip64 extensible data'
    fpin.seek(offset - _END_ARCHIVE64_LOCATOR_SIZE - _END_ARCHIVE64_SIZE, 2)
    data = fpin.read(_END_ARCHIVE64_SIZE)
    if len(data) != _END_ARCHIVE64_SIZE:
        return endrec

    (
    sig,
    sz,
    create_version,
    read_version,
    disk_num,
    disk_dir,
    dircount,
    dircount2,
    dirsize,
    diroffset
    ) = struct.unpack(_END_ARCHIVE64_STRUCT, data)

    if sig != _END_ARCHIVE64_STRING:
        return endrec

    # Update the original endrec using data from the ZIP64 record
    endrec[_ECD_SIGNATURE] = sig
    endrec[_ECD_DISK_NUMBER] = disk_num
    endrec[_ECD_DISK_START] = disk_dir
    endrec[_ECD_ENTRIES_THIS_DISK] = dircount
    endrec[_ECD_ENTRIES_TOTAL] = dircount2
    endrec[_ECD_SIZE] = dirsize
    endrec[_ECD_OFFSET] = diroffset
    return endrec


def _extract_end_archive(fpin) -> TEndArchive | None:
    """Return data from the "End of Central Directory" record, or None.

    The data is a list of the nine items in the ZIP "End of central dir"
    record followed by a tenth item, the file seek offset of this record."""

    # Determine file size
    fpin.seek(0, 2)
    filesize = fpin.tell()

    # Check to see if this is ZIP file with no archive comment (the
    # "end of central directory" structure should be the last item in the
    # file if this is the case).
    try:
        fpin.seek(-_END_ARCHIVE_SIZE, 2)
    except OSError:
        return None

    endrec: TEndArchive

    data = fpin.read()
    if (len(data) == _END_ARCHIVE_SIZE and
            data[0:4] == _END_ARCHIVE_STRING and
            data[-2:] == b"\000\000"):
        # the signature is correct and there's no comment, unpack structure
        endrec = list(struct.unpack(_END_ARCHIVE_STRUCT, data))
        # Append a blank comment and record start offset
        endrec.append(b"")
        endrec.append(filesize - _END_ARCHIVE_SIZE)
        # Try to read the "Zip64 end of central directory" structure
        return _end_archive_update_zip64(fpin, -_END_ARCHIVE_SIZE, endrec)

    # Either this is not a ZIP file, or it is a ZIP file with an archive
    # comment.  Search the end of the file for the "end of central directory"
    # record signature. The comment is the last item in the ZIP file and may be
    # up to 64K long.  It is assumed that the "end of central directory" magic
    # number does not appear in the comment.
    comment_max_start = max(filesize - (1 << 16) - _END_ARCHIVE_SIZE, 0)
    fpin.seek(comment_max_start, 0)

    data = fpin.read()
    start = data.rfind(_END_ARCHIVE_STRING)
    if start >= 0:
        # found the magic number; attempt to unpack and interpret
        recData = data[start: start + _END_ARCHIVE_SIZE]
        if len(recData) != _END_ARCHIVE_SIZE:
            # Zip file is corrupted.
            return None

        endrec = list(struct.unpack(_END_ARCHIVE_STRUCT, recData))
        commentSize = endrec[_ECD_COMMENT_SIZE] #as claimed by the zip file
        comment = data[start+_END_ARCHIVE_SIZE:start+_END_ARCHIVE_SIZE+commentSize]
        endrec.append(comment)
        endrec.append(comment_max_start + start)

        # Try to read the "Zip64 end of central directory" structure
        return _end_archive_update_zip64(fpin,
                comment_max_start + start - filesize,
                endrec,
                )
    # Unable to find a valid end of central directory structure
    return None

# def _sanitize_filename(filename):
#     """Terminate the file name at the first null byte and
#     ensure paths always use forward slashes as the directory separator."""

#     # Terminate the file name at the first null byte.  Null bytes in file
#     # names are used as tricks by viruses in archives.
#     null_byte = filename.find(chr(0))
#     if null_byte >= 0:
#         filename = filename[0:null_byte]
#     # This is used to ensure paths in generated ZIP files always use
#     # forward slashes as the directory separator, as required by the
#     # ZIP format specification.
#     if os.sep != "/" and os.sep in filename:
#         filename = filename.replace(os.sep, "/")
#     if os.altsep and os.altsep != "/" and os.altsep in filename:
#         filename = filename.replace(os.altsep, "/")
#     return filename


class ZipInfoRO:
    """Class with attributes describing each file in the ZIP archive."""

    __slots__ = (
        # 'orig_filename',
        'filename',
        # 'date_time',
        # 'compress_type',
        # '_compresslevel',
        # 'comment',
        # 'extra',
        # 'create_system',
        # 'create_version',
        # 'extract_version',
        # 'reserved',
        'flag_bits',
        # 'volume',
        # 'internal_attr',
        # 'external_attr',
        'header_offset',
        # 'CRC',
        'compress_size',
        'file_size',
        # '_raw_time',
    )

    def __init__(self,
            filename: str = "NoName",
            # date_time=(1980,1,1,0,0,0),
            ):

        self.filename = filename
        # self.orig_filename = filename   # Original file name in archive
        # # Terminate the file name at the first null byte and
        # # ensure paths always use forward slashes as the directory separator.
        # filename = _sanitize_filename(filename)

        # self.filename = filename        # Normalized file name
        # self.date_time = date_time      # year, month, day, hour, min, sec

        # if date_time[0] < 1980:
        #     raise ValueError('ZIP does not support timestamps before 1980')

        # Standard values:
        # self.compress_type = ZIP_STORED # Type of compression for the file
        # self._compresslevel = None      # Level for the compressor
        # self.comment = b""              # Comment for each file
        # self.extra = b""                # ZIP extra data
        # if sys.platform == 'win32':
        #     self.create_system = 0
        # else: # Assume everything else is unix-y
        #     self.create_system = 3

        # self.create_version = DEFAULT_VERSION  # Version which created ZIP archive
        # self.extract_version = DEFAULT_VERSION # Version needed to extract archive
        # self.reserved = 0               # Must be zero
        self.flag_bits = 0              # ZIP flag bits
        # self.volume = 0                 # Volume number of file header
        # self.internal_attr = 0          # Internal attributes
        # self.external_attr = 0          # External file attributes
        self.compress_size = 0          # Size of the compressed file
        self.file_size = 0              # Size of the uncompressed file
        # Other attributes are set by class ZipFileRO:
        # header_offset         Byte offset to the file header
        # CRC                   CRC-32 of the uncompressed file

    # def __repr__(self):
    #     result = ['<%s filename=%r' % (self.__class__.__name__, self._file_name)]
    #     if self.compress_type != ZIP_STORED:
    #         result.append(' compress_type=%s' %
    #                       compressor_names.get(self.compress_type,
    #                                            self.compress_type))
    #     hi = self.external_attr >> 16
    #     lo = self.external_attr & 0xFFFF
    #     if hi:
    #         result.append(' filemode=%r' % stat.filemode(hi))
    #     if lo:
    #         result.append(' external_attr=%#x' % lo)
    #     isdir = self.is_dir()
    #     if not isdir or self.file_size:
    #         result.append(' file_size=%r' % self.file_size)
    #     if ((not isdir or self.compress_size) and
    #         (self.compress_type != ZIP_STORED or
    #          self.file_size != self.compress_size)):
    #         result.append(' compress_size=%r' % self.compress_size)
    #     result.append('>')
    #     return ''.join(result)

    # def FileHeader(self, zip64=None):
    #     """Return the per-file header as a bytes object.

    #     When the optional zip64 arg is None rather than a bool, we will
    #     decide based upon the file_size and compress_size, if known,
    #     False otherwise.
    #     """
    #     dt = self.date_time
    #     dosdate = (dt[0] - 1980) << 9 | dt[1] << 5 | dt[2]
    #     dostime = dt[3] << 11 | dt[4] << 5 | (dt[5] // 2)
    #     if self.flag_bits & _MASK_USE_DATA_DESCRIPTOR:
    #         # Set these to zero because we write them after the file data
    #         CRC = compress_size = file_size = 0
    #     else:
    #         CRC = self.CRC
    #         compress_size = self.compress_size
    #         file_size = self.file_size

    #     extra = self.extra

    #     min_version = 0
    #     if zip64 is None:
    #         # We always explicitly pass zip64 within this module.... This
    #         # remains for anyone using ZipInfoRO.FileHeader as a public API.
    #         zip64 = file_size > ZIP64_LIMIT or compress_size > ZIP64_LIMIT
    #     if zip64:
    #         fmt = '<HHQQ'
    #         extra = extra + struct.pack(fmt,
    #                                     1, struct.calcsize(fmt)-4, file_size, compress_size)
    #         file_size = 0xffffffff
    #         compress_size = 0xffffffff
    #         min_version = ZIP64_VERSION

    #     if self.compress_type == ZIP_BZIP2:
    #         min_version = max(BZIP2_VERSION, min_version)
    #     elif self.compress_type == ZIP_LZMA:
    #         min_version = max(LZMA_VERSION, min_version)

    #     self.extract_version = max(min_version, self.extract_version)
    #     self.create_version = max(min_version, self.create_version)
    #     filename, flag_bits = self._encodeFilenameFlags()
    #     header = struct.pack(_FILE_HEADER_STRUCT, _FILE_HEADER_STRING,
    #                          self.extract_version, self.reserved, flag_bits,
    #                          self.compress_type, dostime, dosdate, CRC,
    #                          compress_size, file_size,
    #                          len(filename), len(extra))
    #     return header + filename + extra

    # def _encodeFilenameFlags(self):
    #     try:
    #         return self._file_name.encode('ascii'), self.flag_bits
    #     except UnicodeEncodeError:
    #         return self._file_name.encode('utf-8'), self.flag_bits | _MASK_UTF_FILENAME

    # def _decodeExtra(self, filename_crc):
    #     # Try to decode the extra field.
    #     extra = self.extra
    #     unpack = struct.unpack
    #     while len(extra) >= 4:
    #         tp, ln = unpack('<HH', extra[:4])
    #         if ln+4 > len(extra):
    #             raise BadZipFile("Corrupt extra field %04x (size=%d)" % (tp, ln))
    #         if tp == 0x0001:
    #             data = extra[4:ln+4]
    #             # ZIP64 extension (large files and/or large archives)
    #             try:
    #                 if self.file_size in (0xFFFF_FFFF_FFFF_FFFF, 0xFFFF_FFFF):
    #                     field = "File size"
    #                     self.file_size, = unpack('<Q', data[:8])
    #                     data = data[8:]
    #                 if self.compress_size == 0xFFFF_FFFF:
    #                     field = "Compress size"
    #                     self.compress_size, = unpack('<Q', data[:8])
    #                     data = data[8:]
    #                 if self.header_offset == 0xFFFF_FFFF:
    #                     field = "Header offset"
    #                     self.header_offset, = unpack('<Q', data[:8])
    #             except struct.error:
    #                 raise BadZipFile(f"Corrupt zip64 extra field. "
    #                                  f"{field} not found.") from None
    #         elif tp == 0x7075:
    #             data = extra[4:ln+4]
    #             # Unicode Path Extra Field
    #             try:
    #                 up_version, up_name_crc = unpack('<BL', data[:5])
    #                 if up_version == 1 and up_name_crc == filename_crc:
    #                     up_unicode_name = data[5:].decode('utf-8')
    #                     if up_unicode_name:
    #                         self.filename = _sanitize_filename(up_unicode_name)
    #                     else:
    #                         import warnings
    #                         warnings.warn("Empty unicode path extra field (0x7075)", stacklevel=2)
    #             except struct.error as e:
    #                 raise BadZipFile("Corrupt unicode path extra field (0x7075)") from e
    #             except UnicodeDecodeError as e:
    #                 raise BadZipFile('Corrupt unicode path extra field (0x7075): invalid utf-8 bytes') from e

    #         extra = extra[ln+4:]

    # @classmethod
    # def from_file(cls, filename, arcname=None, *, strict_timestamps=True):
    #     """Construct an appropriate ZipInfoRO for a file on the filesystem.

    #     filename should be the path to a file or directory on the filesystem.

    #     arcname is the name which it will have within the archive (by default,
    #     this will be the same as filename, but without a drive letter and with
    #     leading path separators removed).
    #     """
    #     if isinstance(filename, os.PathLike):
    #         filename = os.fspath(filename)
    #     st = os.stat(filename)
    #     isdir = stat.S_ISDIR(st.st_mode)
    #     mtime = time.localtime(st.st_mtime)
    #     date_time = mtime[0:6]
    #     if not strict_timestamps and date_time[0] < 1980:
    #         date_time = (1980, 1, 1, 0, 0, 0)
    #     elif not strict_timestamps and date_time[0] > 2107:
    #         date_time = (2107, 12, 31, 23, 59, 59)
    #     # Create ZipInfoRO instance to store file information
    #     if arcname is None:
    #         arcname = filename
    #     arcname = os.path.normpath(os.path.splitdrive(arcname)[1])
    #     while arcname[0] in (os.sep, os.altsep):
    #         arcname = arcname[1:]
    #     if isdir:
    #         arcname += '/'
    #     zinfo = cls(arcname, date_time)
    #     zinfo.external_attr = (st.st_mode & 0xFFFF) << 16  # Unix attributes
    #     if isdir:
    #         zinfo.file_size = 0
    #         zinfo.external_attr |= 0x10  # MS-DOS directory flag
    #     else:
    #         zinfo.file_size = st.st_size

    #     return zinfo

    # def is_dir(self):
    #     """Return True if this archive member is a directory."""
    #     return self._file_name.endswith('/')


# ZIP encryption uses the CRC32 one-byte primitive for scrambling some
# internal keys. We noticed that a direct implementation is faster than
# relying on binascii.crc32().

# _crctable = None
# def _gen_crc(crc):
#     for j in range(8):
#         if crc & 1:
#             crc = (crc >> 1) ^ 0xEDB88320
#         else:
#             crc >>= 1
#     return crc

# ZIP supports a password-based form of encryption. Even though known
# plaintext attacks have been found against it, it is still useful
# to be able to get data out of such a file.
#
# Usage:
#     zd = _ZipDecrypter(mypwd)
#     plain_bytes = zd(cypher_bytes)

# def _ZipDecrypter(pwd):
#     key0 = 305419896
#     key1 = 591751049
#     key2 = 878082192

#     global _crctable
#     if _crctable is None:
#         _crctable = list(map(_gen_crc, range(256)))
#     crctable = _crctable

#     def crc32(ch, crc):
#         """Compute the CRC32 primitive on one byte."""
#         return (crc >> 8) ^ crctable[(crc ^ ch) & 0xFF]

#     def update_keys(c):
#         nonlocal key0, key1, key2
#         key0 = crc32(c, key0)
#         key1 = (key1 + (key0 & 0xFF)) & 0xFFFFFFFF
#         key1 = (key1 * 134775813 + 1) & 0xFFFFFFFF
#         key2 = crc32(key1 >> 24, key2)

#     for p in pwd:
#         update_keys(p)

#     def decrypter(data):
#         """Decrypt a bytes object."""
#         result = bytearray()
#         append = result.append
#         for c in data:
#             k = key2 | 2
#             c ^= ((k * (k^1)) >> 8) & 0xFF
#             update_keys(c)
#             append(c)
#         return bytes(result)

#     return decrypter


class LZMACompressor:

    def __init__(self):
        self._comp = None

    def _init(self):
        props = lzma._encode_filter_properties({'id': lzma.FILTER_LZMA1})
        self._comp = lzma.LZMACompressor(lzma.FORMAT_RAW, filters=[
            lzma._decode_filter_properties(lzma.FILTER_LZMA1, props)
        ])
        return struct.pack('<BBH', 9, 4, len(props)) + props

    def compress(self, data):
        if self._comp is None:
            return self._init() + self._comp.compress(data)
        return self._comp.compress(data)

    def flush(self):
        if self._comp is None:
            return self._init() + self._comp.flush()
        return self._comp.flush()


class LZMADecompressor:

    def __init__(self):
        self._decomp = None
        self._unconsumed = b''
        self.eof = False

    def decompress(self, data):
        if self._decomp is None:
            self._unconsumed += data
            if len(self._unconsumed) <= 4:
                return b''
            psize, = struct.unpack('<H', self._unconsumed[2:4])
            if len(self._unconsumed) <= 4 + psize:
                return b''

            self._decomp = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[
                lzma._decode_filter_properties(lzma.FILTER_LZMA1,
                                               self._unconsumed[4:4 + psize])
            ])
            data = self._unconsumed[4 + psize:]
            del self._unconsumed

        result = self._decomp.decompress(data)
        self.eof = self._decomp.eof
        return result


compressor_names = {
    0: 'store',
    1: 'shrink',
    2: 'reduce',
    3: 'reduce',
    4: 'reduce',
    5: 'reduce',
    6: 'implode',
    7: 'tokenize',
    8: 'deflate',
    9: 'deflate64',
    10: 'implode',
    12: 'bzip2',
    14: 'lzma',
    18: 'terse',
    19: 'lz77',
    97: 'wavpack',
    98: 'ppmd',
}

# def _check_compression(compression):
#     if compression == ZIP_STORED:
#         pass
#     elif compression == ZIP_DEFLATED:
#         if not zlib:
#             raise RuntimeError(
#                 "Compression requires the (missing) zlib module")
#     elif compression == ZIP_BZIP2:
#         if not bz2:
#             raise RuntimeError(
#                 "Compression requires the (missing) bz2 module")
#     elif compression == ZIP_LZMA:
#         if not lzma:
#             raise RuntimeError(
#                 "Compression requires the (missing) lzma module")
#     else:
#         raise NotImplementedError("That compression method is not supported")


# def _get_compressor(compress_type, compresslevel=None):
#     if compress_type == ZIP_DEFLATED:
#         if compresslevel is not None:
#             return zlib.compressobj(compresslevel, zlib.DEFLATED, -15)
#         return zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
#     elif compress_type == ZIP_BZIP2:
#         if compresslevel is not None:
#             return bz2.BZ2Compressor(compresslevel)
#         return bz2.BZ2Compressor()
#     # compresslevel is ignored for ZIP_LZMA
#     elif compress_type == ZIP_LZMA:
#         return LZMACompressor()
#     else:
#         return None


# def _get_decompressor(compress_type):
#     _check_compression(compress_type)
#     if compress_type == ZIP_STORED:
#         return None
#     elif compress_type == ZIP_DEFLATED:
#         return zlib.decompressobj(-15)
#     elif compress_type == ZIP_BZIP2:
#         return bz2.BZ2Decompressor()
#     elif compress_type == ZIP_LZMA:
#         return LZMADecompressor()
#     else:
#         descr = compressor_names.get(compress_type)
#         if descr:
#             raise NotImplementedError("compression type %d (%s)" % (compress_type, descr))
#         else:
#             raise NotImplementedError("compression type %d" % (compress_type,))


class _SharedFileRO:
    __slots__ = (
        '_file',
        '_pos',
        '_close',
    )
    def __init__(self, file, pos, close):
        self._file = file
        self._pos = pos
        self._close = close # callable
        # self._lock = lock
        # self.seekable = file.seekable

    @property
    def seekable(self):
        return self._file.seekable

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence=0) -> int:
        # with self._lock:
            # if self._writing():
            #     raise ValueError("Can't reposition in the ZIP file while "
            #             "there is an open writing handle on it. "
            #             "Close the writing handle before trying to read.")
        self._file.seek(offset, whence)
        self._pos = self._file.tell()
        return self._pos

    def read(self, n: int = -1):
        # with self._lock:
            # if self._writing():
            #     raise ValueError("Can't read from the ZIP file while there "
            #             "is an open writing handle on it. "
            #             "Close the writing handle before trying to read.")
        self._file.seek(self._pos)
        data = self._file.read(n)
        self._pos = self._file.tell()
        return data

    def close(self) -> None:
        if self._file is not None:
            file = self._file
            self._file = None
            self._close(file)

# Provide the tell method for unseekable stream
# class _Tellable:
#     def __init__(self, fp):
#         self._file = fp
#         self.offset = 0

#     def write(self, data):
#         n = self._file.write(data)
#         self.offset += n
#         return n

#     def tell(self):
#         return self.offset

#     def flush(self):
#         self._file.flush()

#     def close(self):
#         self._file.close()


class ZipFilePartRO(io.BufferedIOBase):
    """File-like object for reading an archive member.
       Is returned by ZipFileRO.open().
    """

    # Max size supported by decompressor.
    MAX_N = 1 << 31 - 1

    # Read from compressed files in 4k blocks.
    MIN_READ_SIZE = 4096

    # Chunk size to read during seek
    MAX_SEEK_READ = 1 << 24

    def __init__(self,
                fileobj,
                zipinfo,
                close_fileobj=False,
                ):
        self._fileobj = fileobj
        self._close_fileobj = close_fileobj

        # self._compress_type = zipinfo.compress_type
        self._compress_left = zipinfo.compress_size
        self._left = zipinfo.file_size

        # self._decompressor = None # _get_decompressor(self._compress_type)

        self._eof = False
        self._readbuffer = b''
        self._offset = 0

        self.newlines = None

        # self.mode = mode
        self.name = zipinfo.filename

        # if hasattr(zipinfo, 'CRC'):
        #     self._expected_crc = zipinfo.CRC
        #     # self._running_crc = crc32(b'')
        # else:
        #     self._expected_crc = None

        self._seekable = False
        if fileobj.seekable():
            self._orig_compress_start = fileobj.tell()
            self._orig_compress_size = zipinfo.compress_size
            self._orig_file_size = zipinfo.file_size
            self._seekable = True
        else:
            raise NotImplementedError('not expecting an unseekable file')


        # self._decrypter = None
        # if pwd:
        #     if zipinfo.flag_bits & _MASK_USE_DATA_DESCRIPTOR:
        #         # compare against the file type from extended local headers
        #         check_byte = (zipinfo._raw_time >> 8) & 0xff
        #     else:
        #         # compare against the CRC otherwise
        #         check_byte = (zipinfo.CRC >> 24) & 0xff
        #     h = self._init_decrypter()
        #     if h != check_byte:
        #         raise RuntimeError("Bad password for file %r" % zipinfo.orig_filename)


    # def _init_decrypter(self):
    #     self._decrypter = _ZipDecrypter(self._pwd)
    #     # The first 12 bytes in the cypher stream is an encryption header
    #     #  used to strengthen the algorithm. The first 11 bytes are
    #     #  completely random, while the 12th contains the MSB of the CRC,
    #     #  or the MSB of the file time depending on the header type
    #     #  and is used to check the correctness of the password.
    #     header = self._fileobj.read(12)
    #     self._compress_left -= 12
    #     return self._decrypter(header)[11]

    def __repr__(self):
        result = ['<%s.%s' % (self.__class__.__module__,
                              self.__class__.__qualname__)]
        if not self.closed:
            result.append(' name=%r mode=%r' % (self.name, self.mode))
            # if self._compress_type != ZIP_STORED:
            #     result.append(' compress_type=%s' %
            #                   compressor_names.get(self._compress_type,
            #                                        self._compress_type))
        else:
            result.append(' [closed]')
        result.append('>')
        return ''.join(result)

    # def readline(self, limit=-1):
    #     """Read and return a line from the stream.

    #     If limit is specified, at most limit bytes will be read.
    #     """

    #     if limit < 0:
    #         # Shortcut common case - newline found in buffer.
    #         i = self._readbuffer.find(b'\n', self._offset) + 1
    #         if i > 0:
    #             line = self._readbuffer[self._offset: i]
    #             self._offset = i
    #             return line

    #     return io.BufferedIOBase.readline(self, limit)

    def peek(self, n=1):
        """Returns buffered bytes without advancing the position."""
        if n > len(self._readbuffer) - self._offset:
            chunk = self.read(n)
            if len(chunk) > self._offset:
                self._readbuffer = chunk + self._readbuffer[self._offset:]
                self._offset = 0
            else:
                self._offset -= len(chunk)

        # Return up to 512 bytes to reduce allocation overhead for tight loops.
        return self._readbuffer[self._offset: self._offset + 512]

    def readable(self):
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        return True

    def read(self, n=-1):
        """Read and return up to n bytes.
        If the argument is omitted, None, or negative, data is read and returned until EOF is reached.
        """
        if self.closed:
            raise ValueError("read from closed file.")

        if n is None or n < 0:
            buf = self._readbuffer[self._offset:]
            self._readbuffer = b''
            self._offset = 0
            while not self._eof:
                buf += self._read1(self.MAX_N)
            return buf

        end = n + self._offset
        if end < len(self._readbuffer):
            buf = self._readbuffer[self._offset:end]
            self._offset = end
            return buf

        n = end - len(self._readbuffer)
        buf = self._readbuffer[self._offset:]
        self._readbuffer = b''
        self._offset = 0
        while n > 0 and not self._eof:
            data = self._read1(n)
            if n < len(data):
                self._readbuffer = data
                self._offset = n
                buf += data[:n]
                break
            buf += data
            n -= len(data)
        return buf

    # def _update_crc(self, newdata):
    #     # Update the CRC using the given data.
    #     if self._expected_crc is None:
    #         # No need to compute the CRC if we don't have a reference value
    #         return
    #     self._running_crc = crc32(newdata, self._running_crc)
    #     # Check the CRC if we're at the end of the file
    #     if self._eof and self._running_crc != self._expected_crc:
    #         raise BadZipFile("Bad CRC-32 for file %r" % self.name)

    # def read1(self, n):
    #     """Read up to n bytes with at most one read() system call."""

    #     if n is None or n < 0:
    #         buf = self._readbuffer[self._offset:]
    #         self._readbuffer = b''
    #         self._offset = 0
    #         while not self._eof:
    #             data = self._read1(self.MAX_N)
    #             if data:
    #                 buf += data
    #                 break
    #         return buf

    #     end = n + self._offset
    #     if end < len(self._readbuffer):
    #         buf = self._readbuffer[self._offset:end]
    #         self._offset = end
    #         return buf

    #     n = end - len(self._readbuffer)
    #     buf = self._readbuffer[self._offset:]
    #     self._readbuffer = b''
    #     self._offset = 0
    #     if n > 0:
    #         while not self._eof:
    #             data = self._read1(n)
    #             if n < len(data):
    #                 self._readbuffer = data
    #                 self._offset = n
    #                 buf += data[:n]
    #                 break
    #             if data:
    #                 buf += data
    #                 break
    #     return buf

    def _read2(self, n):
        if self._compress_left <= 0:
            return b''

        n = max(n, self.MIN_READ_SIZE)
        n = min(n, self._compress_left)

        data = self._fileobj.read(n)
        self._compress_left -= len(data)
        if not data:
            raise EOFError

        # if self._decrypter is not None:
        #     data = self._decrypter(data)
        return data



    def _read1(self, n):
        # Read up to n compressed bytes with at most one read() system call,
        # decrypt and decompress them.
        if self._eof or n <= 0:
            return b''

        # Read from file.
        # if self._compress_type == ZIP_DEFLATED:
        #     ## Handle unconsumed data.
        #     data = self._decompressor.unconsumed_tail
        #     if n > len(data):
        #         data += self._read2(n - len(data))
        # else:
        data = self._read2(n)

        # if self._compress_type == ZIP_STORED:
        self._eof = self._compress_left <= 0
        # elif self._compress_type == ZIP_DEFLATED:
        #     n = max(n, self.MIN_READ_SIZE)
        #     data = self._decompressor.decompress(data, n)
        #     self._eof = (self._decompressor.eof or
        #                  self._compress_left <= 0 and
        #                  not self._decompressor.unconsumed_tail)
        #     if self._eof:
        #         data += self._decompressor.flush()
        # else:
        #     data = self._decompressor.decompress(data)
        #     self._eof = self._decompressor.eof or self._compress_left <= 0

        data = data[:self._left]
        self._left -= len(data)
        if self._left <= 0:
            self._eof = True
        # self._update_crc(data)
        return data


    def close(self):
        try:
            if self._close_fileobj:
                self._fileobj.close()
        finally:
            super().close()

    def seekable(self):
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        return self._seekable

    def seek(self, offset, whence=os.SEEK_SET):
        if self.closed:
            raise ValueError("seek on closed file.")
        if not self._seekable:
            raise io.UnsupportedOperation("underlying stream is not seekable")
        curr_pos = self.tell()
        if whence == os.SEEK_SET:
            new_pos = offset
        elif whence == os.SEEK_CUR:
            new_pos = curr_pos + offset
        elif whence == os.SEEK_END:
            new_pos = self._orig_file_size + offset
        else:
            raise ValueError("whence must be os.SEEK_SET (0), "
                             "os.SEEK_CUR (1), or os.SEEK_END (2)")

        if new_pos > self._orig_file_size:
            new_pos = self._orig_file_size

        if new_pos < 0:
            new_pos = 0

        read_offset = new_pos - curr_pos
        buff_offset = read_offset + self._offset

        if buff_offset >= 0 and buff_offset < len(self._readbuffer):
            # Just move the _offset index if the new position is in the _readbuffer
            self._offset = buff_offset
            read_offset = 0

        # Fast seek uncompressed unencrypted file
        elif self._compress_type == ZIP_STORED and read_offset > 0:
            # disable CRC checking after first seeking - it would be invalid
            self._expected_crc = None
            # seek actual file taking already buffered data into account
            read_offset -= len(self._readbuffer) - self._offset
            self._fileobj.seek(read_offset, os.SEEK_CUR)
            self._left -= read_offset
            read_offset = 0
            # flush read buffer
            self._readbuffer = b''
            self._offset = 0

        elif read_offset < 0:
            # Position is before the current position. Reset the ZipFilePartRO
            self._fileobj.seek(self._orig_compress_start)
            # self._running_crc = self._orig_start_crc
            # self._expected_crc = self._orig_crc
            self._compress_left = self._orig_compress_size
            self._left = self._orig_file_size
            self._readbuffer = b''
            self._offset = 0
            # self._decompressor = None # _get_decompressor(self._compress_type)
            self._eof = False
            read_offset = new_pos
            # if self._decrypter is not None:
            #     self._init_decrypter()

        while read_offset > 0:
            read_len = min(self.MAX_SEEK_READ, read_offset)
            self.read(read_len)
            read_offset -= read_len

        return self.tell()

    def tell(self):
        if self.closed:
            raise ValueError("tell on closed file.")
        if not self._seekable:
            raise io.UnsupportedOperation("underlying stream is not seekable")
        filepos = self._orig_file_size - self._left - len(self._readbuffer) + self._offset
        return filepos


# class _ZipWriteFile(io.BufferedIOBase):
#     def __init__(self, zf, zinfo, zip64):
#         self._zinfo = zinfo
#         self._zip64 = zip64
#         self._zipfile = zf
#         self._compressor = _get_compressor(zinfo.compress_type,
#                                            zinfo._compresslevel)
#         self._file_size = 0
#         self._compress_size = 0
#         self._crc = 0

#     @property
#     def _fileobj(self):
#         return self._zipfile.fp

#     def writable(self):
#         return True

#     def write(self, data):
#         if self.closed:
#             raise ValueError('I/O operation on closed file.')

#         # Accept any data that supports the buffer protocol
#         if isinstance(data, (bytes, bytearray)):
#             nbytes = len(data)
#         else:
#             data = memoryview(data)
#             nbytes = data.nbytes
#         self._file_size += nbytes

#         self._crc = crc32(data, self._crc)
#         if self._compressor:
#             data = self._compressor.compress(data)
#             self._compress_size += len(data)
#         self._fileobj.write(data)
#         return nbytes

#     def close(self):
#         if self.closed:
#             return
#         try:
#             super().close()
#             # Flush any data from the compressor, and update header info
#             if self._compressor:
#                 buf = self._compressor.flush()
#                 self._compress_size += len(buf)
#                 self._fileobj.write(buf)
#                 self._zinfo.compress_size = self._compress_size
#             else:
#                 self._zinfo.compress_size = self._file_size
#             self._zinfo.CRC = self._crc
#             self._zinfo.file_size = self._file_size

#             if not self._zip64:
#                 if self._file_size > ZIP64_LIMIT:
#                     raise RuntimeError("File size too large, try using force_zip64")
#                 if self._compress_size > ZIP64_LIMIT:
#                     raise RuntimeError("Compressed size too large, try using force_zip64")

#             # Write updated header info
#             if self._zinfo.flag_bits & _MASK_USE_DATA_DESCRIPTOR:
#                 # Write CRC and file sizes after the file data
#                 fmt = '<LLQQ' if self._zip64 else '<LLLL'
#                 self._fileobj.write(struct.pack(fmt, _DD_SIGNATURE, self._zinfo.CRC,
#                     self._zinfo.compress_size, self._zinfo.file_size))
#                 self._zipfile.start_dir = self._fileobj.tell()
#             else:
#                 # Seek backwards and write file header (which will now include
#                 # correct CRC and file sizes)

#                 # Preserve current position in file
#                 self._zipfile.start_dir = self._fileobj.tell()
#                 self._fileobj.seek(self._zinfo.header_offset)
#                 self._fileobj.write(self._zinfo.FileHeader(self._zip64))
#                 self._fileobj.seek(self._zipfile.start_dir)

#             # Successfully written: Add file to our caches
#             self._zipfile.filelist.append(self._zinfo)
#             self._zipfile.NameToInfo[self._zinfo.filename] = self._zinfo
#         finally:
#             self._zipfile._writing = False



class ZipFileRO:

    # fp = None                   # Set here since __del__ checks it
    # _windows_illegal_name_trans_table = None
    # compression = ZIP_STORED
    # mode = 'r'

    __slots__ = (
        '_start_dir',
        '_name_to_info',
        '_file_passed',
        '_file_name',
        '_file',
        '_file_ref_count',
    )

    def _read_contents(self):
        """Read in the table of contents for the ZIP file."""
        fp = self._file
        try:
            endrec: TEndArchive = _extract_end_archive(fp)
        except OSError:
            raise BadZipFile("File is not a zip file")
        if not endrec:
            raise BadZipFile("File is not a zip file")

        size_cd = endrec[_ECD_SIZE]             # bytes in central directory
        offset_cd = endrec[_ECD_OFFSET]         # offset of central directory
        # self._comment = endrec[_ECD_COMMENT]    # archive comment

        # "concat" is zero, unless zip was concatenated to another file
        concat = endrec[_ECD_LOCATION] - size_cd - offset_cd
        if endrec[_ECD_SIGNATURE] == _END_ARCHIVE64_STRING:
            # If Zip64 extension structures are present, account for them
            concat -= (_END_ARCHIVE64_SIZE + _END_ARCHIVE64_LOCATOR_SIZE)

        # self._start_dir:  Position of start of central directory
        self._start_dir = offset_cd + concat
        if self._start_dir < 0:
            raise BadZipFile("Bad offset for central directory")

        fp.seek(self._start_dir, 0)
        data = fp.read(size_cd)
        fp = io.BytesIO(data)

        total = 0
        filename_length = 0
        extra_length = 0
        comment_length = 0

        while total < size_cd:
            cdir = fp.read(_CENTRAL_DIR_SIZE)
            if len(cdir) != _CENTRAL_DIR_SIZE:
                raise BadZipFile("Truncated central directory")

            cdir = struct.unpack(_CENTRAL_DIR_STRUCT, cdir)
            if cdir[_CD_SIGNATURE] != _CENTRAL_DIR_STRING:
                raise BadZipFile("Bad magic number for central directory")

            filename_length = cdir[_CD_FILENAME_LENGTH]
            filename = fp.read(filename_length)
            # orig_filename_crc = crc32(filename)
            flags = cdir[_CD_FLAG_BITS]

            if flags & _MASK_UTF_FILENAME:
                # UTF-8 file names extension
                filename = filename.decode('utf-8')
            else:
                # Historical ZIP filename encoding
                filename = filename.decode('cp437')

            # Create ZipInfoRO instance to store file information
            zinfo = ZipInfoRO(filename)
            extra_length = cdir[_CD_EXTRA_FIELD_LENGTH]
            _ = fp.read(extra_length)
            comment_length = cdir[_CD_COMMENT_LENGTH]
            _ = fp.read(comment_length)

            zinfo.header_offset = cdir[_CD_LOCAL_HEADER_OFFSET]

            # (_, _, _, _,
            #  zinfo.flag_bits, zinfo.compress_type, _, _,
            # _, zinfo.compress_size, zinfo.file_size) = cdir[1:12]
            zinfo.flag_bits = cdir[5]

            compress_type = cdir[6]
            assert compress_type == ZIP_STORED

            zinfo.compress_size = cdir[10]
            zinfo.file_size = cdir[11]


            # if zinfo.extract_version > MAX_EXTRACT_VERSION:
            #     raise NotImplementedError("zip file version %.1f" %
            #                               (zinfo.extract_version / 10))

            # zinfo.volume, zinfo.internal_attr, zinfo.external_attr = cdir[15:18]
            # Convert date/time code to (year, month, day, hour, min, sec)
            # zinfo._raw_time = t
            # zinfo.date_time = ( (d>>9)+1980, (d>>5)&0xF, d&0x1F,
            #                 t>>11, (t>>5)&0x3F, (t&0x1F) * 2 )
            # zinfo._decodeExtra(orig_filename_crc)
            zinfo.header_offset = zinfo.header_offset + concat

            # self.filelist.append(x)
            self._name_to_info[zinfo.filename] = zinfo

            # update total bytes read from central directory
            total = (
                    total +
                    _CENTRAL_DIR_SIZE +
                    filename_length +
                    extra_length +
                    comment_length
                    )


    def __init__(self,
                file,
                # mode="r",
                # compression=ZIP_STORED,
                # allowZip64=True,
                # compresslevel=None,
                # *,
                # strict_timestamps=True,
                # metadata_encoding=None,
                ):
        """Open the ZIP file with mode read 'r', write 'w', exclusive create 'x',
        or append 'a'."""
        # if mode not in ('r', 'w', 'x', 'a'):
        #     raise ValueError("ZipFileRO requires mode 'r', 'w', 'x', or 'a'")

        # _check_compression(compression)

        # self._allowZip64 = True
        # self._didModify = False

        self._name_to_info = {}    # Find file info given name
        # self.filelist = []      # List of ZipInfoRO instances for archive
        # self.compression = compression  # Method of compression
        # self.compresslevel = compresslevel
        # self.mode = 'r'
        # self.pwd = None
        # self._comment = b''
        # self._strict_timestamps = strict_timestamps
        # self.metadata_encoding = metadata_encoding

        # Check that we don't try to write with nonconforming codecs
        # if self.metadata_encoding and mode != 'r':
        #     raise ValueError(
        #         "metadata_encoding is only supported for reading files")

        # Check if we were passed a file-like object
        if isinstance(file, os.PathLike):
            file = os.fspath(file)

        if isinstance(file, str):
            # No, it's a filename
            self._file_passed = False
            self._file_name = file
            # modeDict = {'r' : 'rb', 'w': 'w+b', 'x': 'x+b', 'a' : 'r+b',
            #             'r+b': 'w+b', 'w+b': 'wb', 'x+b': 'xb'}
            # filemode = modeDict[mode]
            self._file = io.open(file, 'rb')
            # while True:
            #     try:
            #         self._file = io.open(file, filemode)
            #     except OSError:
            #         if filemode in modeDict:
            #             filemode = modeDict[filemode]
            #             continue
            #         raise
            #     break
        else:
            self._file_passed = True
            self._file = file
            self._file_name = getattr(file, 'name', None)

        self._file_ref_count = 1
        # self._lock = threading.RLock()
        # self._seekable = True
        # self._writing = False

        try:
            self._read_contents()
            # elif mode in ('w', 'x'):
            #     # set the modified flag so central directory gets written
            #     # even if no files are added to the archive
            #     self._didModify = True
            #     try:
            #         self._start_dir = self._file.tell()
            #     except (AttributeError, OSError):
            #         self._file = _Tellable(self._file)
            #         self._start_dir = 0
            #         self._seekable = False
            #     else:
            #         # Some file-like objects can provide tell() but not seek()
            #         try:
            #             self._file.seek(self._start_dir)
            #         except (AttributeError, OSError):
            #             self._seekable = False
            # elif mode == 'a':
            #     try:
            #         # See if file is a zip file
            #         self._read_contents()
            #         # seek to start of directory and overwrite
            #         self._file.seek(self._start_dir)
            #     except BadZipFile:
            #         # file is not a zip file, just append
            #         self._file.seek(0, 2)

            #         # set the modified flag so central directory gets written
            #         # even if no files are added to the archive
            #         self._didModify = True
            #         self._start_dir = self._file.tell()
            # else:
            #     raise ValueError("Mode must be 'r', 'w', 'x', or 'a'")
        except:
            fp = self._file
            self._file = None
            self._close(fp)
            raise

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.close()

    def __repr__(self):
        result = [f'<{self.__class__.__name__}']

        if self._file is not None:
            if self._file_passed:
                result.append(' file=%r' % self._file)
            elif self._file_name is not None:
                result.append(' filename=%r' % self._file_name)
        else:
            result.append(' [closed]')
        result.append('>')
        return ''.join(result)


    def namelist(self):
        """Return a list of file names in the archive."""
        # return [data.filename for data in self.filelist]
        return list(self._name_to_info.keys())

    def infolist(self):
        """Return a list of class ZipInfoRO instances for files in the
        archive."""
        # return self.filelist
        return self._name_to_info.values()

    # def printdir(self, file=None):
    #     """Print a table of contents for the zip file."""
    #     print("%-46s %19s %12s" % ("File Name", "Modified    ", "Size"),
    #           file=file)
    #     for zinfo in self.filelist:
    #         date = "%d-%02d-%02d %02d:%02d:%02d" % zinfo.date_time[:6]
    #         print("%-46s %s %12d" % (zinfo.filename, date, zinfo.file_size),
    #               file=file)

    # def testzip(self):
    #     """Read all the files and check the CRC.

    #     Return None if all files could be read successfully, or the name
    #     of the offending file otherwise."""
    #     chunk_size = 2 ** 20
    #     for zinfo in self.filelist:
    #         try:
    #             # Read by chunks, to avoid an OverflowError or a
    #             # MemoryError with very large embedded files.
    #             with self.open(zinfo.filename, "r") as f:
    #                 while f.read(chunk_size):     # Check CRC-32
    #                     pass
    #         except BadZipFile:
    #             return zinfo.filename

    def getinfo(self, name: str):
        """Return the instance of ZipInfoRO given 'name'."""
        info = self._name_to_info.get(name)
        if info is None:
            raise KeyError(
                'There is no item named %r in the archive' % name)
        return info

    # def setpassword(self, pwd):
    #     """Set default password for encrypted files."""
    #     if pwd and not isinstance(pwd, bytes):
    #         raise TypeError("pwd: expected bytes, got %s" % type(pwd).__name__)
    #     if pwd:
    #         self.pwd = pwd
    #     else:
    #         self.pwd = None

    # @property
    # def comment(self):
    #     """The comment text associated with the ZIP file."""
    #     return self._comment

    # @comment.setter
    # def comment(self, comment):
    #     if not isinstance(comment, bytes):
    #         raise TypeError("comment: expected bytes, got %s" % type(comment).__name__)
    #     # check for valid comment length
    #     if len(comment) > ZIP_MAX_COMMENT:
    #         import warnings
    #         warnings.warn('Archive comment is too long; truncating to %d bytes'
    #                       % ZIP_MAX_COMMENT, stacklevel=2)
    #         comment = comment[:ZIP_MAX_COMMENT]
    #     self._comment = comment
    #     self._didModify = True

    # def read(self, name, pwd=None):
    #     """Return file bytes for name."""
    #     with self.open(name, "r", pwd) as fp:
    #         return fp.read()

    def open(self, name: str | ZipInfoRO) -> ZipFilePartRO:
        """Return file-like object for 'name'.

        name is a string for the file name within the ZIP file, or a ZipInfoRO
        object.

        mode should be 'r' to read a file already in the ZIP file, or 'w' to
        write to a file newly added to the archive.

        pwd is the password to decrypt files (only used for reading).

        When writing, if the file size is not known in advance but may exceed
        2 GiB, pass force_zip64 to use the ZIP64 format, which can handle large
        files.  If the size is known in advance, it is best to pass a ZipInfoRO
        instance for name, with zinfo.file_size set.
        """
        # if mode not in {"r", "w"}:
        #     raise ValueError('open() requires mode "r" or "w"')
        # if pwd and (mode == "w"):
        #     raise ValueError("pwd is only supported for reading files")
        if not self._file:
            raise ValueError("Attempt to use ZIP archive that was already closed")

        # Make sure we have an info object
        if isinstance(name, ZipInfoRO):
            zinfo = name
        else:
            zinfo = self.getinfo(name)

        # if mode == 'w':
        #     return self._open_to_write(zinfo, force_zip64=force_zip64)

        # if self._writing:
        #     raise ValueError("Can't read from the ZIP file while there "
        #             "is an open writing handle on it. "
        #             "Close the writing handle before trying to read.")

        # Open for reading:
        self._file_ref_count += 1
        zef_file = _SharedFileRO(self._file,
                zinfo.header_offset,
                self._close,
                # self._lock,
                )
        try:
            # Skip the file header:
            fheader = zef_file.read(_FILE_HEADER_SIZE)
            if len(fheader) != _FILE_HEADER_SIZE:
                raise BadZipFile("Truncated file header")

            fheader = struct.unpack(_FILE_HEADER_STRUCT, fheader)
            if fheader[_FH_SIGNATURE] != _FILE_HEADER_STRING:
                raise BadZipFile("Bad magic number for file header")

            fname = zef_file.read(fheader[_FH_FILENAME_LENGTH])
            if fheader[_FH_EXTRA_FIELD_LENGTH]:
                zef_file.seek(fheader[_FH_EXTRA_FIELD_LENGTH], whence=1)

            if zinfo.flag_bits & _MASK_COMPRESSED_PATCH:
                # Zip 2.7: compressed patched data
                raise NotImplementedError("compressed patched data (flag bit 5)")

            if zinfo.flag_bits & _MASK_STRONG_ENCRYPTION:
                # strong encryption
                raise NotImplementedError("strong encryption (flag bit 6)")

            if fheader[_FH_GENERAL_PURPOSE_FLAG_BITS] & _MASK_UTF_FILENAME:
                # UTF-8 filename
                fname_str = fname.decode("utf-8")
            else:
                fname_str = fname.decode("cp437")

            if fname_str != zinfo.filename:
                raise BadZipFile(
                    'File name in directory %r and header %r differ.'
                    % (zinfo.filename, fname))

            # check for encrypted flag & handle password
            is_encrypted = zinfo.flag_bits & _MASK_ENCRYPTED
            if is_encrypted:
                raise NotImplementedError()

            #     if not pwd:
            #         pwd = self.pwd
            #     if pwd and not isinstance(pwd, bytes):
            #         raise TypeError("pwd: expected bytes, got %s" % type(pwd).__name__)
            #     if not pwd:
            #         raise RuntimeError("File %r is encrypted, password "
            #                            "required for extraction" % name)
            # else:
            #     pwd = None

            return ZipFilePartRO(zef_file, zinfo, True)
        except:
            zef_file.close()
            raise

    # def _open_to_write(self, zinfo, force_zip64=False):
    #     if force_zip64 and not self._allowZip64:
    #         raise ValueError(
    #             "force_zip64 is True, but allowZip64 was False when opening "
    #             "the ZIP file."
    #         )
    #     if self._writing:
    #         raise ValueError("Can't write to the ZIP file while there is "
    #                          "another write handle open on it. "
    #                          "Close the first handle before opening another.")

    #     # Size and CRC are overwritten with correct data after processing the file
    #     zinfo.compress_size = 0
    #     zinfo.CRC = 0

    #     zinfo.flag_bits = 0x00
    #     if zinfo.compress_type == ZIP_LZMA:
    #         # Compressed data includes an end-of-stream (EOS) marker
    #         zinfo.flag_bits |= _MASK_COMPRESS_OPTION_1
    #     if not self._seekable:
    #         zinfo.flag_bits |= _MASK_USE_DATA_DESCRIPTOR

    #     if not zinfo.external_attr:
    #         zinfo.external_attr = 0o600 << 16  # permissions: ?rw-------

    #     # Compressed size can be larger than uncompressed size
    #     zip64 = force_zip64 or (zinfo.file_size * 1.05 > ZIP64_LIMIT)
    #     if not self._allowZip64 and zip64:
    #         raise LargeZipFile("Filesize would require ZIP64 extensions")

    #     if self._seekable:
    #         self._file.seek(self._start_dir)
    #     zinfo.header_offset = self._file.tell()

    #     self._writecheck(zinfo)
    #     self._didModify = True

    #     self._file.write(zinfo.FileHeader(zip64))

    #     self._writing = True
    #     return _ZipWriteFile(self, zinfo, zip64)

    # def extract(self, member, path=None, pwd=None):
    #     """Extract a member from the archive to the current working directory,
    #        using its full name. Its file information is extracted as accurately
    #        as possible. `member' may be a filename or a ZipInfoRO object. You can
    #        specify a different directory using `path'.
    #     """
    #     if path is None:
    #         path = os.getcwd()
    #     else:
    #         path = os.fspath(path)

    #     return self._extract_member(member, path, pwd)

    # def extractall(self, path=None, members=None, pwd=None):
    #     """Extract all members from the archive to the current working
    #        directory. `path' specifies a different directory to extract to.
    #        `members' is optional and must be a subset of the list returned
    #        by namelist().
    #     """
    #     if members is None:
    #         members = self.namelist()

    #     if path is None:
    #         path = os.getcwd()
    #     else:
    #         path = os.fspath(path)

    #     for zipinfo in members:
    #         self._extract_member(zipinfo, path, pwd)

    # @classmethod
    # def _sanitize_windows_name(cls, arcname, pathsep):
    #     """Replace bad characters and remove trailing dots from parts."""
    #     table = cls._windows_illegal_name_trans_table
    #     if not table:
    #         illegal = ':<>|"?*'
    #         table = str.maketrans(illegal, '_' * len(illegal))
    #         cls._windows_illegal_name_trans_table = table
    #     arcname = arcname.translate(table)
    #     # remove trailing dots and spaces
    #     arcname = (x.rstrip(' .') for x in arcname.split(pathsep))
    #     # rejoin, removing empty parts.
    #     arcname = pathsep.join(x for x in arcname if x)
    #     return arcname

    # def _extract_member(self, member, targetpath, pwd):
    #     """Extract the ZipInfoRO object 'member' to a physical
    #        file on the path targetpath.
    #     """
    #     if not isinstance(member, ZipInfoRO):
    #         member = self.getinfo(member)

    #     # build the destination pathname, replacing
    #     # forward slashes to platform specific separators.
    #     arcname = member.filename.replace('/', os.path.sep)

    #     if os.path.altsep:
    #         arcname = arcname.replace(os.path.altsep, os.path.sep)
    #     # interpret absolute pathname as relative, remove drive letter or
    #     # UNC path, redundant separators, "." and ".." components.
    #     arcname = os.path.splitdrive(arcname)[1]
    #     invalid_path_parts = ('', os.path.curdir, os.path.pardir)
    #     arcname = os.path.sep.join(x for x in arcname.split(os.path.sep)
    #                                if x not in invalid_path_parts)
    #     if os.path.sep == '\\':
    #         # filter illegal characters on Windows
    #         arcname = self._sanitize_windows_name(arcname, os.path.sep)

    #     if not arcname and not member.is_dir():
    #         raise ValueError("Empty filename.")

    #     targetpath = os.path.join(targetpath, arcname)
    #     targetpath = os.path.normpath(targetpath)

    #     # Create all upper directories if necessary.
    #     upperdirs = os.path.dirname(targetpath)
    #     if upperdirs and not os.path.exists(upperdirs):
    #         os.makedirs(upperdirs)

    #     if member.is_dir():
    #         if not os.path.isdir(targetpath):
    #             os.mkdir(targetpath)
    #         return targetpath

    #     with self.open(member, pwd=pwd) as source, \
    #          open(targetpath, "wb") as target:
    #         shutil.copyfileobj(source, target)

    #     return targetpath

    # def _writecheck(self, zinfo):
    #     """Check for errors before writing a file to the archive."""
    #     if zinfo.filename in self._name_to_info:
    #         import warnings
    #         warnings.warn('Duplicate name: %r' % zinfo.filename, stacklevel=3)
    #     if self.mode not in ('w', 'x', 'a'):
    #         raise ValueError("write() requires mode 'w', 'x', or 'a'")
    #     if not self._file:
    #         raise ValueError(
    #             "Attempt to write ZIP archive that was already closed")
    #     _check_compression(zinfo.compress_type)
    #     if not self._allowZip64:
    #         requires_zip64 = None
    #         if len(self.filelist) >= ZIP_FILECOUNT_LIMIT:
    #             requires_zip64 = "Files count"
    #         elif zinfo.file_size > ZIP64_LIMIT:
    #             requires_zip64 = "Filesize"
    #         elif zinfo.header_offset > ZIP64_LIMIT:
    #             requires_zip64 = "Zipfile size"
    #         if requires_zip64:
    #             raise LargeZipFile(requires_zip64 +
    #                                " would require ZIP64 extensions")

    # def write(self, filename, arcname=None,
    #           compress_type=None, compresslevel=None):
    #     """Put the bytes from filename into the archive under the name
    #     arcname."""
    #     if not self._file:
    #         raise ValueError(
    #             "Attempt to write to ZIP archive that was already closed")
    #     if self._writing:
    #         raise ValueError(
    #             "Can't write to ZIP archive while an open writing handle exists"
    #         )

    #     zinfo = ZipInfoRO.from_file(filename, arcname,
    #                               strict_timestamps=self._strict_timestamps)

    #     if zinfo.is_dir():
    #         zinfo.compress_size = 0
    #         zinfo.CRC = 0
    #         self.mkdir(zinfo)
    #     else:
    #         if compress_type is not None:
    #             zinfo.compress_type = compress_type
    #         else:
    #             zinfo.compress_type = self.compression

    #         if compresslevel is not None:
    #             zinfo._compresslevel = compresslevel
    #         else:
    #             zinfo._compresslevel = self.compresslevel

    #         with open(filename, "rb") as src, self.open(zinfo, 'w') as dest:
    #             shutil.copyfileobj(src, dest, 1024*8)

    # def writestr(self, zinfo_or_arcname, data,
    #              compress_type=None, compresslevel=None):
    #     """Write a file into the archive.  The contents is 'data', which
    #     may be either a 'str' or a 'bytes' instance; if it is a 'str',
    #     it is encoded as UTF-8 first.
    #     'zinfo_or_arcname' is either a ZipInfoRO instance or
    #     the name of the file in the archive."""
    #     if isinstance(data, str):
    #         data = data.encode("utf-8")
    #     if not isinstance(zinfo_or_arcname, ZipInfoRO):
    #         zinfo = ZipInfoRO(filename=zinfo_or_arcname,
    #                         date_time=time.localtime(time.time())[:6])
    #         zinfo.compress_type = self.compression
    #         zinfo._compresslevel = self.compresslevel
    #         if zinfo.filename.endswith('/'):
    #             zinfo.external_attr = 0o40775 << 16   # drwxrwxr-x
    #             zinfo.external_attr |= 0x10           # MS-DOS directory flag
    #         else:
    #             zinfo.external_attr = 0o600 << 16     # ?rw-------
    #     else:
    #         zinfo = zinfo_or_arcname

    #     if not self._file:
    #         raise ValueError(
    #             "Attempt to write to ZIP archive that was already closed")
    #     if self._writing:
    #         raise ValueError(
    #             "Can't write to ZIP archive while an open writing handle exists."
    #         )

    #     if compress_type is not None:
    #         zinfo.compress_type = compress_type

    #     if compresslevel is not None:
    #         zinfo._compresslevel = compresslevel

    #     zinfo.file_size = len(data)            # Uncompressed size
    #     with self._lock:
    #         with self.open(zinfo, mode='w') as dest:
    #             dest.write(data)

    # def mkdir(self, zinfo_or_directory_name, mode=511):
    #     """Creates a directory inside the zip archive."""
    #     if isinstance(zinfo_or_directory_name, ZipInfoRO):
    #         zinfo = zinfo_or_directory_name
    #         if not zinfo.is_dir():
    #             raise ValueError("The given ZipInfoRO does not describe a directory")
    #     elif isinstance(zinfo_or_directory_name, str):
    #         directory_name = zinfo_or_directory_name
    #         if not directory_name.endswith("/"):
    #             directory_name += "/"
    #         zinfo = ZipInfoRO(directory_name)
    #         zinfo.compress_size = 0
    #         zinfo.CRC = 0
    #         zinfo.external_attr = ((0o40000 | mode) & 0xFFFF) << 16
    #         zinfo.file_size = 0
    #         zinfo.external_attr |= 0x10
    #     else:
    #         raise TypeError("Expected type str or ZipInfoRO")

    #     with self._lock:
    #         if self._seekable:
    #             self._file.seek(self._start_dir)
    #         zinfo.header_offset = self._file.tell()  # Start of header bytes
    #         if zinfo.compress_type == ZIP_LZMA:
    #         # Compressed data includes an end-of-stream (EOS) marker
    #             zinfo.flag_bits |= _MASK_COMPRESS_OPTION_1

    #         self._writecheck(zinfo)
    #         self._didModify = True

    #         self.filelist.append(zinfo)
    #         self._name_to_info[zinfo.filename] = zinfo
    #         self._file.write(zinfo.FileHeader(False))
    #         self._start_dir = self._file.tell()

    def __del__(self):
        """Call the "close()" method in case the user forgot."""
        self.close()

    def close(self):
        """Close the file, and for mode 'w', 'x' and 'a' write the ending
        records."""
        if self._file is None:
            return

        # if self._writing:
        #     raise ValueError("Can't close the ZIP file while there is "
        #                      "an open writing handle on it. "
        #                      "Close the writing handle before closing the zip.")

        # try:
        #     if self.mode in ('w', 'x', 'a') and self._didModify: # write ending records
        #         with self._lock:
        #             if self._seekable:
        #                 self._file.seek(self._start_dir)
        #             self._write_end_record()
        # finally:
        file = self._file
        self._file = None
        self._close(file)

    # def _write_end_record(self):
    #     for zinfo in self.filelist:         # write central directory
    #         dt = zinfo.date_time
    #         dosdate = (dt[0] - 1980) << 9 | dt[1] << 5 | dt[2]
    #         dostime = dt[3] << 11 | dt[4] << 5 | (dt[5] // 2)
    #         extra = []
    #         if zinfo.file_size > ZIP64_LIMIT \
    #            or zinfo.compress_size > ZIP64_LIMIT:
    #             extra.append(zinfo.file_size)
    #             extra.append(zinfo.compress_size)
    #             file_size = 0xffffffff
    #             compress_size = 0xffffffff
    #         else:
    #             file_size = zinfo.file_size
    #             compress_size = zinfo.compress_size

    #         if zinfo.header_offset > ZIP64_LIMIT:
    #             extra.append(zinfo.header_offset)
    #             header_offset = 0xffffffff
    #         else:
    #             header_offset = zinfo.header_offset

    #         extra_data = zinfo.extra
    #         min_version = 0
    #         if extra:
    #             # Append a ZIP64 field to the extra's
    #             extra_data = _Extra.strip(extra_data, (1,))
    #             extra_data = struct.pack(
    #                 '<HH' + 'Q'*len(extra),
    #                 1, 8*len(extra), *extra) + extra_data

    #             min_version = ZIP64_VERSION

    #         if zinfo.compress_type == ZIP_BZIP2:
    #             min_version = max(BZIP2_VERSION, min_version)
    #         elif zinfo.compress_type == ZIP_LZMA:
    #             min_version = max(LZMA_VERSION, min_version)

    #         extract_version = max(min_version, zinfo.extract_version)
    #         create_version = max(min_version, zinfo.create_version)
    #         filename, flag_bits = zinfo._encodeFilenameFlags()
    #         centdir = struct.pack(_CENTRAL_DIR_STRUCT,
    #                               _CENTRAL_DIR_STRING, create_version,
    #                               zinfo.create_system, extract_version, zinfo.reserved,
    #                               flag_bits, zinfo.compress_type, dostime, dosdate,
    #                               zinfo.CRC, compress_size, file_size,
    #                               len(filename), len(extra_data), len(zinfo.comment),
    #                               0, zinfo.internal_attr, zinfo.external_attr,
    #                               header_offset)
    #         self._file.write(centdir)
    #         self._file.write(filename)
    #         self._file.write(extra_data)
    #         self._file.write(zinfo.comment)

    #     pos2 = self._file.tell()
    #     # Write end-of-zip-archive record
    #     centDirCount = len(self.filelist)
    #     centDirSize = pos2 - self._start_dir
    #     centDirOffset = self._start_dir
    #     requires_zip64 = None
    #     if centDirCount > ZIP_FILECOUNT_LIMIT:
    #         requires_zip64 = "Files count"
    #     elif centDirOffset > ZIP64_LIMIT:
    #         requires_zip64 = "Central directory offset"
    #     elif centDirSize > ZIP64_LIMIT:
    #         requires_zip64 = "Central directory size"
    #     if requires_zip64:
    #         # Need to write the ZIP64 end-of-archive records
    #         if not self._allowZip64:
    #             raise LargeZipFile(requires_zip64 +
    #                                " would require ZIP64 extensions")
    #         zip64endrec = struct.pack(
    #             _END_ARCHIVE64_STRUCT, _END_ARCHIVE64_STRING,
    #             44, 45, 45, 0, 0, centDirCount, centDirCount,
    #             centDirSize, centDirOffset)
    #         self._file.write(zip64endrec)

    #         zip64locrec = struct.pack(
    #             _END_ARCHIVE64_LOCATOR_STRUCT,
    #             _END_ARCHIVE64_LOCATOR_STRING, 0, pos2, 1)
    #         self._file.write(zip64locrec)
    #         centDirCount = min(centDirCount, 0xFFFF)
    #         centDirSize = min(centDirSize, 0xFFFFFFFF)
    #         centDirOffset = min(centDirOffset, 0xFFFFFFFF)

    #     endrec = struct.pack(_END_ARCHIVE_STRUCT, _END_ARCHIVE_STRING,
    #                          0, 0, centDirCount, centDirCount,
    #                          centDirSize, centDirOffset, len(self._comment))
    #     self._file.write(endrec)
    #     self._file.write(self._comment)
    #     if self.mode == "a":
    #         self._file.truncate()
    #     self._file.flush()

    def _close(self, file: tp.IO[bytes]):
        assert self._file_ref_count > 0
        self._file_ref_count -= 1
        if not self._file_ref_count and not self._file_passed:
            file.close()

