"""Nonblocking, process-lifetime ownership on local POSIX and Windows stores.

POSIX uses flock with no-follow. Windows uses a non-inheritable CreateFileW
handle with sharing disabled and opens reparse points themselves for rejection.
Closing the descriptor, including process exit, releases ownership. Lock files
remain as inert markers; deleting them is never the recovery protocol.
"""
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import stat


class OwnerBusy(ValueError):
    """Another descriptor already owns this local resource."""


def available():
    return os.name=='nt' or os.name=='posix' and importlib.util.find_spec('fcntl') is not None


def is_redirect(path):
    try:
        value=Path(path).lstat()
    except FileNotFoundError:return False
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value,'st_file_attributes',0)&0x400)


def directory(path):
    path=Path(path);path.mkdir(mode=0o700,exist_ok=True)
    if is_redirect(path):raise ValueError('owner directory must not be a symlink or reparse point')
    return path


def _windows_open(path):
    import ctypes
    from ctypes import wintypes as w
    import msvcrt
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW
    create.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE]
    create.restype=w.HANDLE
    close=kernel.CloseHandle;close.argtypes=[w.HANDLE];close.restype=w.BOOL
    class Info(ctypes.Structure):
        _fields_=[('attributes',w.DWORD),('created',w.FILETIME),('accessed',w.FILETIME),('written',w.FILETIME),
            ('volume',w.DWORD),('size_high',w.DWORD),('size_low',w.DWORD),('links',w.DWORD),('index_high',w.DWORD),('index_low',w.DWORD)]
    info=kernel.GetFileInformationByHandle;info.argtypes=[w.HANDLE,ctypes.POINTER(Info)];info.restype=w.BOOL
    absolute=os.path.abspath(path)
    if not absolute.startswith('\\\\?\\'):
        absolute='\\\\?\\UNC\\'+absolute[2:] if absolute.startswith('\\\\') else '\\\\?\\'+absolute
    # GENERIC_READ|WRITE, no sharing, OPEN_ALWAYS, OPEN_REPARSE_POINT.
    handle=create(absolute,0xC0000000,0,None,4,0x00200080,None)
    if handle==w.HANDLE(-1).value:
        error=ctypes.get_last_error()
        if error in (32,33):raise OwnerBusy('local resource already has an active owner')
        raise ctypes.WinError(error)
    try:
        value=Info()
        if not info(handle,ctypes.byref(value)):raise ctypes.WinError(ctypes.get_last_error())
        if value.attributes&(0x400|0x10):raise ValueError('owner file must be a regular file, not a reparse point')
        fd=msvcrt.open_osfhandle(handle,os.O_RDWR|getattr(os,'O_BINARY',0)|getattr(os,'O_NOINHERIT',0))
        handle=None
        return fd
    finally:
        if handle is not None:close(handle)


def acquire(path):
    path=Path(path)
    if is_redirect(path):raise ValueError('owner file must not be a symlink or reparse point')
    if os.name=='nt':fd=_windows_open(path)
    elif os.name=='posix':
        import fcntl
        fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd);raise OwnerBusy('local resource already has an active owner') from exc
        except BaseException:
            os.close(fd);raise
    else:raise ValueError('local execution ownership is unsupported on this platform')
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('owner file must be a regular file')
        os.set_inheritable(fd,False)
        return fd
    except BaseException:
        os.close(fd);raise


@contextmanager
def owned(path):
    fd=acquire(path)
    try:yield
    finally:os.close(fd)
