import tempfile
import typing as tp
from io import BytesIO
from io import StringIO
from pathlib import Path
from urllib import request
from zipfile import ZipFile
import os


class StringIOTemporaryFile(StringIO):
    '''Subclass of a StringIO that reads from a managed file that is deleted when this instance goes out of scope.
    '''

    def __init__(self, fp: Path) -> None:
        self._fp = fp
        self._file = open(fp, 'r')
        super().__init__()

    def __del__(self) -> None:
        self._file.close()
        os.unlink(self._fp)
        super().__del__()

    def seek(self, offset: int) -> int:
        return self._file.seek(offset)

    def read(self, size=-1) -> str:
        return self._file.read(size)

    def readline(self, size=-1) -> str:
        return self._file.readline(size)

    def __iter__(self) -> tp.Iterator[str]:
        return self._file.__iter__()

class BytesIOTemporaryFile(BytesIO):
    '''Subclass of a BytesIO that reads from a managed file that is deleted when this instance goes out of scope.
    '''

    def __init__(self, fp: Path) -> None:
        self._fp = fp
        self._file = open(fp, 'rb')
        super().__init__()

    def __del__(self) -> None:
        self._file.close()
        os.unlink(self._fp)
        super().__del__()

    def seek(self, offset: int) -> int:
        return self._file.seek(offset)

    def read(self, size=-1) -> str:
        return self._file.read(size)

    def readline(self, size=-1) -> str:
        return self._file.readline(size)

    def __iter__(self) -> tp.Iterator[str]:
        return self._file.__iter__()


def url_adapter_file(
        url: str,
        encoding: tp.Optional[str] = 'utf-8',
        in_memory: bool = True,
        buffer_size: int = 8192,
        ) -> tp.Union[Path, StringIO, BytesIO]:

    with request.urlopen(url) as response:
        if in_memory:
            if encoding:
                return StringIO(response.read().decode(encoding))
            else:
                return BytesIO(response.read())

        # not in-memory, write a file
        with tempfile.NamedTemporaryFile(mode='w' if encoding else 'wb',
                suffix=None,
                delete=False,
                ) as f:
            fp = Path(f.name)
            if encoding:
                extract = lambda: response.read(buffer_size).decode(encoding)
            else:
                extract = lambda: response.read(buffer_size)

            while True:
                b = extract()
                if b:
                    f.write(b)
                else:
                    break
            if encoding:
                return StringIOTemporaryFile(fp)
            return BytesIOTemporaryFile(fp)


def url_adapter_zip(
        url: str,
        encoding: tp.Optional[str] = 'utf-8',
        in_memory: bool = True,
        buffer_size: int = 8192,
        ) -> tp.Union[Path, StringIO, BytesIO]:

    with request.urlopen(url) as response:
        if in_memory:
            archive = BytesIO(response.read())
        else:
            with tempfile.NamedTemporaryFile(mode='wb',
                    suffix='zip',
                    delete=False,
                    ) as f:
                archive = Path(f.name)
                while True:
                    b = response.read(buffer_size)
                    if b:
                        f.write(b)
                    else:
                        break

    with ZipFile(archive) as zf:
        names = zf.namelist()
        if len(names) > 1:
            raise RuntimeError(f'more than one file found in zip archive: {names}')
        name = names.pop()
        data = zf.read(name)

    if in_memory:
        if encoding:
            return StringIO(data.decode(encoding))
        else:
            return BytesIO(data)

    # not in-memory, write a file, delete archive
    os.unlink(archive)

    with tempfile.NamedTemporaryFile(mode='w' if encoding else 'wb',
            suffix=None,
            delete=False,
            ) as f:
        fp = Path(f.name)
        if encoding:
            f.write(data.decode(encoding))
        else:
            f.write(data)

        if encoding:
            return StringIOTemporaryFile(fp)
        return BytesIOTemporaryFile(fp)


def URL(url: str,
        *,
        encoding: tp.Optional[str] = 'utf-8',
        in_memory: bool = True,
        buffer_size: int = 8192,
        ) -> tp.Union[Path, StringIO, BytesIO]:
    '''
    Args:
        encoding: Defaults to UTF-8; if None, binary data is collected.
        in_memory: if True, data is loaded into memory; if False, a temporary file is written.
    '''
    if url.endswith('.zip'):
        return url_adapter_zip(url, encoding, in_memory, buffer_size)
    return url_adapter_file(url, encoding, in_memory, buffer_size)



