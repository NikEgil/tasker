"""Системные диалоги выбора файла и папки (Windows, без внешних зависимостей)."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path


def choose_directory(initial_path: str | os.PathLike[str] | None = None) -> str | None:
    """Открыть системный диалог выбора папки."""
    if os.name != "nt":
        raise OSError("Системный диалог выбора папки доступен только в Windows")

    from ctypes import wintypes

    class BROWSEINFOW(ctypes.Structure):
        _fields_ = [
            ("hwndOwner", wintypes.HWND),
            ("pidlRoot", wintypes.LPVOID),
            ("pszDisplayName", wintypes.LPWSTR),
            ("lpszTitle", wintypes.LPCWSTR),
            ("ulFlags", wintypes.UINT),
            ("lpfn", wintypes.LPVOID),
            ("lParam", wintypes.LPARAM),
            ("iImage", ctypes.c_int),
        ]

    BIF_RETURNONLYFSDIRS = 0x00000001
    BIF_EDITBOX = 0x00000010
    BIF_NEWDIALOGSTYLE = 0x00000040
    BIF_USENEWUI = BIF_NEWDIALOGSTYLE | BIF_EDITBOX

    display = ctypes.create_unicode_buffer(260)
    title = "Папка Google Диска для Tasker"
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    owner = user32.GetForegroundWindow()
    if not owner:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        owner = kernel32.GetConsoleWindow()

    browse = BROWSEINFOW()
    browse.hwndOwner = owner
    browse.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
    browse.lpszTitle = title
    browse.ulFlags = BIF_RETURNONLYFSDIRS | BIF_USENEWUI

    ole32 = ctypes.windll.ole32
    com_initialized = False
    try:
        result = ole32.CoInitialize(None)
        com_initialized = result in (0, 1)
    except OSError:
        pass

    pidl = None
    try:
        shell32 = ctypes.windll.shell32
        shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
        shell32.SHBrowseForFolderW.restype = wintypes.LPVOID
        shell32.SHGetPathFromIDListW.argtypes = [wintypes.LPVOID, wintypes.LPWSTR]
        shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(browse))
        if not pidl:
            return None
        path_buffer = ctypes.create_unicode_buffer(4096)
        if not shell32.SHGetPathFromIDListW(pidl, path_buffer):
            return None
        return path_buffer.value.strip() or None
    finally:
        if pidl:
            try:
                ole32.CoTaskMemFree.argtypes = [wintypes.LPVOID]
                ole32.CoTaskMemFree(pidl)
            except OSError:
                pass
        if com_initialized:
            try:
                ole32.CoUninitialize()
            except OSError:
                pass


def choose_database_path(initial_path: str | os.PathLike[str] | None = None) -> str | None:
    """Открыть системный диалог сохранения/выбора .db.

    Возвращает путь или None, если пользователь отменил выбор.
    На не-Windows платформах поднимает OSError — интерфейс даст ввести путь
    вручную.
    """
    if os.name != "nt":
        raise OSError("Системный диалог выбора файла доступен только в Windows")

    from ctypes import wintypes

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD),
            ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD),
            ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR),
            ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR),
            ("lCustData", wintypes.LPARAM),
            ("lpfnHook", wintypes.LPVOID),
            ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", wintypes.LPVOID),
            ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD),
        ]

    OFN_HIDEREADONLY = 0x00000004
    OFN_NOCHANGEDIR = 0x00000008
    OFN_PATHMUSTEXIST = 0x00000800
    OFN_EXPLORER = 0x00080000
    OFN_DONTADDTORECENT = 0x02000000

    initial = Path(initial_path).expanduser() if initial_path else None
    buffer = ctypes.create_unicode_buffer(4096)
    if initial:
        buffer.value = str(initial)
    initial_dir = str(initial.parent) if initial and initial.parent.is_dir() else None

    filter_buffer = ctypes.create_unicode_buffer(
        "База Tasker (*.db)\0*.db\0Все файлы (*.*)\0*.*\0\0"
    )
    title = "Файл базы Tasker: выберите существующий или создайте новый"
    default_extension = "db"
    # Диалог должен быть поверх браузера, из которого его вызвали.
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    owner = user32.GetForegroundWindow()
    if not owner:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        owner = kernel32.GetConsoleWindow()

    dialog = OPENFILENAMEW()
    dialog.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    dialog.hwndOwner = owner
    dialog.lpstrFilter = ctypes.cast(filter_buffer, wintypes.LPCWSTR)
    dialog.nFilterIndex = 1
    dialog.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    dialog.nMaxFile = len(buffer)
    dialog.lpstrInitialDir = initial_dir
    dialog.lpstrTitle = title
    dialog.Flags = (
        OFN_EXPLORER
        | OFN_NOCHANGEDIR
        | OFN_PATHMUSTEXIST
        | OFN_HIDEREADONLY
        | OFN_DONTADDTORECENT
    )
    dialog.lpstrDefExt = default_extension

    ole32 = ctypes.windll.ole32
    com_initialized = False
    try:
        result = ole32.CoInitialize(None)
        com_initialized = result in (0, 1)
    except OSError:
        pass

    try:
        comdlg32 = ctypes.windll.comdlg32
        comdlg32.GetSaveFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
        comdlg32.GetSaveFileNameW.restype = wintypes.BOOL
        if not comdlg32.GetSaveFileNameW(ctypes.byref(dialog)):
            error_code = comdlg32.CommDlgExtendedError()
            if error_code:
                raise OSError(f"Диалог выбора файла завершился с ошибкой {error_code}")
            return None
        selected = buffer.value.strip()
        return selected or None
    finally:
        if com_initialized:
            try:
                ole32.CoUninitialize()
            except OSError:
                pass
