"""Who writes this address? A hardware write watchpoint through the Win32 Debug API.

gst attaches as a debugger (as x64dbg or WinDbg would), puts the address in DR0 of every thread (DR7: write,
1/2/4/8 bytes), records each hit (the instruction after the write, the thread, the new value), then clears the
debug registers on every thread while the process is frozen and detaches. The game keeps running and its
memory is never written. 64-bit processes only.
"""

from __future__ import annotations

import ctypes
import time

from gst.common import GsError

LEN_BITS = {1: 0b00, 2: 0b01, 4: 0b11, 8: 0b10}
DR0_MASK = 0b11 | (0b1111 << 16)  # L0/G0 + RW0/LEN0


def _context(handle, flags: int):
    from gst.runtime import win

    c = win.AlignedContext(flags)
    return c if win.GetThreadContext(handle, ctypes.addressof(c.ctx)) else None


def _set(handle, c) -> bool:
    from gst.runtime import win

    return bool(win.SetThreadContext(handle, ctypes.addressof(c.ctx)))


def _arm(handle, address: int, size: int) -> bool:
    from gst.runtime import win

    c = _context(handle, win.CONTEXT_DEBUG_REGISTERS)
    if c is None:
        return False
    c.ctx.Dr0 = address
    c.ctx.Dr7 = (c.ctx.Dr7 & ~DR0_MASK) | 0b1 | (0b01 << 16) | (LEN_BITS[size] << 18)
    c.ctx.Dr6 = 0
    return _set(handle, c)


def _disarm(handle) -> None:
    from gst.runtime import win

    c = _context(handle, win.CONTEXT_DEBUG_REGISTERS)
    if c is not None:
        c.ctx.Dr0 = 0
        c.ctx.Dr7 &= ~DR0_MASK
        c.ctx.Dr6 = 0
        _set(handle, c)


def _is_wow64(pid: int) -> bool:
    from gst.runtime import win

    handle = win.OpenProcess(win.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        flag = win.wt.BOOL()
        return bool(win.IsWow64Process(handle, ctypes.byref(flag)) and flag.value)
    finally:
        win.CloseHandle(handle)


def watch(pid: int, address: int, size: int = 4, *, max_hits: int = 10, seconds: float = 30.0) -> dict:
    """Hits on writes to [address, address+size): RIP after each write, thread id and the bytes then stored."""
    from gst.runtime import memory, win

    if size not in LEN_BITS or address % size:
        raise GsError("watch size is 1, 2, 4 or 8 bytes and the address must be aligned to it")
    if _is_wow64(pid):
        raise GsError("32-bit (WoW64) processes are not supported by gst trace watch yet")
    if not win.DebugActiveProcess(pid):
        raise GsError(f"cannot attach to pid {pid}: {win.last_error()} (another debugger attached?)")
    win.DebugSetProcessKillOnExit(False)
    threads: dict[int, int] = {}
    process = None
    hits: list[dict] = []
    armed = stopping = False
    stopped_by = "time"
    deadline = time.monotonic() + seconds
    event = win.DEBUG_EVENT()
    started = time.monotonic()

    def disarm_all() -> None:
        for handle in threads.values():
            _disarm(handle)

    try:
        while True:
            if not win.WaitForDebugEvent(ctypes.byref(event), 100):
                if not stopping and armed and time.monotonic() > deadline and process:
                    stopping = True  # freeze the game with a break-in, then clean up at that breakpoint
                    win.DebugBreakProcess(process)
                elif stopping and time.monotonic() > deadline + 10:
                    raise GsError("the process did not stop to clear the watchpoint")
                continue
            code, tid = event.dwDebugEventCode, event.dwThreadId
            status = win.DBG_CONTINUE
            if code == win.CREATE_PROCESS_DEBUG_EVENT:
                info = event.u.CreateProcessInfo
                process = info.hProcess
                threads[tid] = info.hThread
                if info.hFile:
                    win.CloseHandle(info.hFile)
            elif code == win.CREATE_THREAD_DEBUG_EVENT:
                threads[tid] = event.u.CreateThread.hThread
                if armed and not stopping:
                    _arm(threads[tid], address, size)
            elif code == win.EXIT_THREAD_DEBUG_EVENT:
                threads.pop(tid, None)
            elif code == win.LOAD_DLL_DEBUG_EVENT:
                if event.u.LoadDll.hFile:
                    win.CloseHandle(event.u.LoadDll.hFile)
            elif code == win.EXIT_PROCESS_DEBUG_EVENT:
                stopped_by = "process exited"
                win.ContinueDebugEvent(event.dwProcessId, tid, win.DBG_CONTINUE)
                threads.clear()
                break
            elif code == win.EXCEPTION_DEBUG_EVENT:
                record = event.u.Exception.ExceptionRecord
                if record.ExceptionCode in (win.EXCEPTION_BREAKPOINT, win.STATUS_WX86_BREAKPOINT):
                    if stopping:
                        disarm_all()
                        win.ContinueDebugEvent(event.dwProcessId, tid, win.DBG_CONTINUE)
                        break
                    if not armed:  # the attach break-in: every thread is frozen here
                        armed = all(_arm(h, address, size) for h in threads.values())
                        if not armed:
                            disarm_all()
                            win.ContinueDebugEvent(event.dwProcessId, tid, win.DBG_CONTINUE)
                            raise GsError(f"could not set the debug registers: {win.last_error()}")
                elif record.ExceptionCode == win.EXCEPTION_SINGLE_STEP and tid in threads:
                    c = _context(threads[tid], win.CONTEXT_DEBUG_REGISTERS | win.CONTEXT_CONTROL)
                    if c is not None and c.ctx.Dr6 & 0x1:
                        value = memory.read(process, address, size) if process else None
                        hits.append({"rip": c.ctx.Rip, "thread": tid, "bytes": value.hex() if value else None,
                                     "at": round(time.monotonic() - started, 3)})
                        c.ctx.Dr6 = 0
                        _set(threads[tid], c)
                        if len(hits) >= max_hits:
                            stopped_by = "hits"
                            disarm_all()
                            win.ContinueDebugEvent(event.dwProcessId, tid, win.DBG_CONTINUE)
                            break
                    else:
                        status = win.DBG_EXCEPTION_NOT_HANDLED
                else:
                    status = win.DBG_EXCEPTION_NOT_HANDLED  # the game's own exceptions go back to it
            win.ContinueDebugEvent(event.dwProcessId, tid, status)
    except BaseException:
        # Ctrl+C or an error while armed: freeze the game once more and clear the registers before detaching
        if armed and process and threads:
            win.DebugBreakProcess(process)
            limit = time.monotonic() + 5
            while time.monotonic() < limit:
                if win.WaitForDebugEvent(ctypes.byref(event), 100):
                    if (event.dwDebugEventCode == win.EXCEPTION_DEBUG_EVENT
                            and event.u.Exception.ExceptionRecord.ExceptionCode == win.EXCEPTION_BREAKPOINT):
                        disarm_all()
                        win.ContinueDebugEvent(event.dwProcessId, event.dwThreadId, win.DBG_CONTINUE)
                        break
                    win.ContinueDebugEvent(event.dwProcessId, event.dwThreadId, win.DBG_CONTINUE)
        raise
    finally:
        win.DebugActiveProcessStop(pid)
    return {"pid": pid, "address": hex(address), "size": size, "hits": hits, "stopped_by": stopped_by,
            "seconds": round(time.monotonic() - started, 1)}
