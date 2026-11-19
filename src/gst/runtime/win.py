"""Win32 definitions for the runtime tools (ctypes, x64): processes, memory, debug events, thread context.

Only what `gst trace` needs: read-only memory access and the Debug API's hardware data breakpoints.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes as wt

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

DWORD64 = ctypes.c_ulonglong
SIZE_T = ctypes.c_size_t

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
TH32CS_SNAPPROCESS = 0x00000002
LIST_MODULES_ALL = 0x03
MEM_COMMIT = 0x1000
MEM_PRIVATE, MEM_MAPPED, MEM_IMAGE = 0x20000, 0x40000, 0x1000000
PAGE_NOACCESS, PAGE_GUARD = 0x01, 0x100
READABLE = {0x02: "r", 0x04: "rw", 0x08: "rw(cow)", 0x20: "rx", 0x40: "rwx", 0x80: "rwx(cow)"}
WRITABLE = {0x04, 0x08, 0x40, 0x80}

# Debug API
DBG_CONTINUE = 0x00010002
DBG_EXCEPTION_NOT_HANDLED = 0x80010001
EXCEPTION_DEBUG_EVENT, CREATE_THREAD_DEBUG_EVENT, CREATE_PROCESS_DEBUG_EVENT = 1, 2, 3
EXIT_THREAD_DEBUG_EVENT, EXIT_PROCESS_DEBUG_EVENT, LOAD_DLL_DEBUG_EVENT = 4, 5, 6
EXCEPTION_BREAKPOINT, EXCEPTION_SINGLE_STEP = 0x80000003, 0x80000004
STATUS_WX86_BREAKPOINT = 0x4000001F
CONTEXT_AMD64 = 0x00100000
CONTEXT_CONTROL = CONTEXT_AMD64 | 0x1
CONTEXT_DEBUG_REGISTERS = CONTEXT_AMD64 | 0x10


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260)]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p), ("AllocationProtect", wt.DWORD),
                ("PartitionId", wt.WORD), ("RegionSize", SIZE_T), ("State", wt.DWORD), ("Protect", wt.DWORD),
                ("Type", wt.DWORD)]


class MODULEINFO(ctypes.Structure):
    _fields_ = [("lpBaseOfDll", ctypes.c_void_p), ("SizeOfImage", wt.DWORD), ("EntryPoint", ctypes.c_void_p)]


class EXCEPTION_RECORD(ctypes.Structure):
    _fields_ = [("ExceptionCode", wt.DWORD), ("ExceptionFlags", wt.DWORD), ("ExceptionRecord", ctypes.c_void_p),
                ("ExceptionAddress", ctypes.c_void_p), ("NumberParameters", wt.DWORD),
                ("ExceptionInformation", ctypes.c_size_t * 15)]


class EXCEPTION_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("ExceptionRecord", EXCEPTION_RECORD), ("dwFirstChance", wt.DWORD)]


class CREATE_THREAD_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("hThread", wt.HANDLE), ("lpThreadLocalBase", ctypes.c_void_p), ("lpStartAddress", ctypes.c_void_p)]


class CREATE_PROCESS_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("hFile", wt.HANDLE), ("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("lpBaseOfImage", ctypes.c_void_p),
                ("dwDebugInfoFileOffset", wt.DWORD), ("nDebugInfoSize", wt.DWORD), ("lpThreadLocalBase", ctypes.c_void_p),
                ("lpStartAddress", ctypes.c_void_p), ("lpImageName", ctypes.c_void_p), ("fUnicode", wt.WORD)]


class LOAD_DLL_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("hFile", wt.HANDLE), ("lpBaseOfDll", ctypes.c_void_p), ("dwDebugInfoFileOffset", wt.DWORD),
                ("nDebugInfoSize", wt.DWORD), ("lpImageName", ctypes.c_void_p), ("fUnicode", wt.WORD)]


class _DEBUG_EVENT_UNION(ctypes.Union):
    _fields_ = [("Exception", EXCEPTION_DEBUG_INFO), ("CreateThread", CREATE_THREAD_DEBUG_INFO),
                ("CreateProcessInfo", CREATE_PROCESS_DEBUG_INFO), ("ExitCode", wt.DWORD), ("LoadDll", LOAD_DLL_DEBUG_INFO)]


class DEBUG_EVENT(ctypes.Structure):
    _fields_ = [("dwDebugEventCode", wt.DWORD), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD),
                ("u", _DEBUG_EVENT_UNION)]


class M128A(ctypes.Structure):
    _fields_ = [("Low", ctypes.c_ulonglong), ("High", ctypes.c_longlong)]


class XMM_SAVE_AREA32(ctypes.Structure):
    _fields_ = [("ControlWord", wt.WORD), ("StatusWord", wt.WORD), ("TagWord", wt.BYTE), ("Reserved1", wt.BYTE),
                ("ErrorOpcode", wt.WORD), ("ErrorOffset", wt.DWORD), ("ErrorSelector", wt.WORD), ("Reserved2", wt.WORD),
                ("DataOffset", wt.DWORD), ("DataSelector", wt.WORD), ("Reserved3", wt.WORD), ("MxCsr", wt.DWORD),
                ("MxCsr_Mask", wt.DWORD), ("FloatRegisters", M128A * 8), ("XmmRegisters", M128A * 16),
                ("Reserved4", wt.BYTE * 96)]


class CONTEXT64(ctypes.Structure):
    _fields_ = [("P1Home", DWORD64), ("P2Home", DWORD64), ("P3Home", DWORD64), ("P4Home", DWORD64), ("P5Home", DWORD64),
                ("P6Home", DWORD64), ("ContextFlags", wt.DWORD), ("MxCsr", wt.DWORD), ("SegCs", wt.WORD),
                ("SegDs", wt.WORD), ("SegEs", wt.WORD), ("SegFs", wt.WORD), ("SegGs", wt.WORD), ("SegSs", wt.WORD),
                ("EFlags", wt.DWORD), ("Dr0", DWORD64), ("Dr1", DWORD64), ("Dr2", DWORD64), ("Dr3", DWORD64),
                ("Dr6", DWORD64), ("Dr7", DWORD64), ("Rax", DWORD64), ("Rcx", DWORD64), ("Rdx", DWORD64), ("Rbx", DWORD64),
                ("Rsp", DWORD64), ("Rbp", DWORD64), ("Rsi", DWORD64), ("Rdi", DWORD64), ("R8", DWORD64), ("R9", DWORD64),
                ("R10", DWORD64), ("R11", DWORD64), ("R12", DWORD64), ("R13", DWORD64), ("R14", DWORD64), ("R15", DWORD64),
                ("Rip", DWORD64), ("FltSave", XMM_SAVE_AREA32), ("VectorRegister", M128A * 26), ("VectorControl", DWORD64),
                ("DebugControl", DWORD64), ("LastBranchToRip", DWORD64), ("LastBranchFromRip", DWORD64),
                ("LastExceptionToRip", DWORD64), ("LastExceptionFromRip", DWORD64)]


assert ctypes.sizeof(CONTEXT64) == 1232, ctypes.sizeof(CONTEXT64)
assert ctypes.sizeof(DEBUG_EVENT) == 176, ctypes.sizeof(DEBUG_EVENT)


class AlignedContext:
    """A CONTEXT64 in a 16-byte aligned buffer (Get/SetThreadContext require the alignment)."""

    def __init__(self, flags: int):
        self._buffer = ctypes.create_string_buffer(ctypes.sizeof(CONTEXT64) + 16)
        address = ctypes.addressof(self._buffer)
        self.ctx = CONTEXT64.from_address(address + (-address % 16))
        self.ctx.ContextFlags = flags

    @property
    def ref(self):
        return ctypes.byref(self.ctx)


def _sig(fn, restype, *argtypes):
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


OpenProcess = _sig(kernel32.OpenProcess, wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
CloseHandle = _sig(kernel32.CloseHandle, wt.BOOL, wt.HANDLE)
CreateToolhelp32Snapshot = _sig(kernel32.CreateToolhelp32Snapshot, wt.HANDLE, wt.DWORD, wt.DWORD)
Process32FirstW = _sig(kernel32.Process32FirstW, wt.BOOL, wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
Process32NextW = _sig(kernel32.Process32NextW, wt.BOOL, wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
QueryFullProcessImageNameW = _sig(kernel32.QueryFullProcessImageNameW, wt.BOOL, wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                  ctypes.POINTER(wt.DWORD))
VirtualQueryEx = _sig(kernel32.VirtualQueryEx, SIZE_T, wt.HANDLE, ctypes.c_void_p,
                      ctypes.POINTER(MEMORY_BASIC_INFORMATION), SIZE_T)
ReadProcessMemory = _sig(kernel32.ReadProcessMemory, wt.BOOL, wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, SIZE_T,
                         ctypes.POINTER(SIZE_T))
IsWow64Process = _sig(kernel32.IsWow64Process, wt.BOOL, wt.HANDLE, ctypes.POINTER(wt.BOOL))
EnumProcessModulesEx = _sig(psapi.EnumProcessModulesEx, wt.BOOL, wt.HANDLE, ctypes.POINTER(wt.HMODULE), wt.DWORD,
                            ctypes.POINTER(wt.DWORD), wt.DWORD)
GetModuleFileNameExW = _sig(psapi.GetModuleFileNameExW, wt.DWORD, wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD)
GetModuleInformation = _sig(psapi.GetModuleInformation, wt.BOOL, wt.HANDLE, wt.HMODULE, ctypes.POINTER(MODULEINFO),
                            wt.DWORD)
DebugActiveProcess = _sig(kernel32.DebugActiveProcess, wt.BOOL, wt.DWORD)
DebugActiveProcessStop = _sig(kernel32.DebugActiveProcessStop, wt.BOOL, wt.DWORD)
DebugSetProcessKillOnExit = _sig(kernel32.DebugSetProcessKillOnExit, wt.BOOL, wt.BOOL)
DebugBreakProcess = _sig(kernel32.DebugBreakProcess, wt.BOOL, wt.HANDLE)
WaitForDebugEvent = _sig(kernel32.WaitForDebugEvent, wt.BOOL, ctypes.POINTER(DEBUG_EVENT), wt.DWORD)
ContinueDebugEvent = _sig(kernel32.ContinueDebugEvent, wt.BOOL, wt.DWORD, wt.DWORD, wt.DWORD)
GetThreadContext = _sig(kernel32.GetThreadContext, wt.BOOL, wt.HANDLE, ctypes.c_void_p)
SetThreadContext = _sig(kernel32.SetThreadContext, wt.BOOL, wt.HANDLE, ctypes.c_void_p)


def last_error() -> str:
    code = ctypes.get_last_error()
    return f"{ctypes.FormatError(code).strip()} (error {code})"
