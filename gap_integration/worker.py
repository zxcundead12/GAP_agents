"""Trusted API operations for the locally installed IPM 7.5 OpenServer.

This worker is private to backend/service. It never accepts a command string
from the model. All output except the final JSON is written to the journal.
"""

from __future__ import annotations

from datetime import datetime, timezone
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sys
import time

PROGID = "PX32.OpenServer.1"
SOFTWARE_VERSION = "IPM 7.5 Build 600 / GAP 8.5"
JOINTS = ("AG_J1", "AG_J2")
PIPE = "AG_P1"


class GapError(Exception):
    def __init__(self, status, message, details=None):
        super().__init__(message)
        self.status, self.details = status, details


def running_gap_pids():
    if os.name != "nt":
        return []
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    kernel.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise GapError("ERROR", "Не удалось проверить существующие процессы GAP")
    item = PROCESSENTRY32W()
    item.dwSize = ctypes.sizeof(item)
    result = []
    try:
        present = kernel.Process32FirstW(snapshot, ctypes.byref(item))
        while present:
            if item.szExeFile.lower() == "gap.exe":
                result.append(int(item.th32ProcessID))
            present = kernel.Process32NextW(snapshot, ctypes.byref(item))
    finally:
        kernel.CloseHandle(snapshot)
    return result


class Client:
    def __init__(self, journal, trace_id):
        self.journal, self.trace_id = Path(journal), trace_id
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            self.server = win32com.client.Dispatch(PROGID, clsctx=pythoncom.CLSCTX_LOCAL_SERVER)
            self.pythoncom = pythoncom
        except Exception as exc:
            raise GapError("COM_UNAVAILABLE", f"OpenServer COM недоступен: {exc}") from exc

    def log(self, method, argument, result=None, error=None, elapsed=None):
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        record = {"trace_id": self.trace_id, "at": datetime.now(timezone.utc).isoformat(), "method": method,
                  "argument": argument, "result": result, "error": error, "elapsed_seconds": elapsed}
        with self.journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def error(self, number):
        try:
            message = str(self.server.GetErrorDescription(number))
        except Exception:
            message = f"OpenServer error {number}"
        return message

    def command_error(self, number, command):
        message = self.error(number)
        # The broker may reject a licence immediately or report the same
        # rejection through GetLastError after the asynchronous call.
        no_license = (number == 22 and command == "GAP.START()"
                      and "openserver license" in message.lower())
        return GapError("LICENSE_UNAVAILABLE" if no_license else "API_ERROR", message,
                        {"native_error": number, "command": command,
                         "model_write_started": False if command == "GAP.START()" else None})

    def get(self, tag):
        before = time.monotonic()
        try:
            value = self.server.GetValue(tag)
            error = int(self.server.GetLastError("GAP"))
            if error:
                raise GapError("API_ERROR", self.error(error), {"native_error": error, "tag": tag})
            value = str(value)
            self.log("GetValue", tag, value, elapsed=time.monotonic()-before)
            return value
        except Exception as exc:
            self.log("GetValue", tag, error=str(exc), elapsed=time.monotonic()-before)
            raise

    def cmd(self, command, timeout=35):
        before = time.monotonic()
        try:
            error = int(self.server.DoCommandAsync(command))
            if error:
                raise self.command_error(error, command)
            while int(self.server.IsBusy("GAP")) > 0:
                if time.monotonic()-before > timeout:
                    raise GapError("TIMEOUT", "OpenServer остаётся занят; GAP не остановлен")
                time.sleep(0.05)
            error = int(self.server.GetLastError("GAP"))
            if error:
                raise self.command_error(error, command)
            self.log("DoCommandAsync", command, {"native_error": 0}, elapsed=time.monotonic()-before)
        except Exception as exc:
            self.log("DoCommandAsync", command, error=str(exc), elapsed=time.monotonic()-before)
            raise

    def set(self, tag, value):
        before = time.monotonic()
        try:
            number = int(self.server.SetValue(tag, value))
            error = int(self.server.GetLastError('GAP'))
            if number or error:
                code = error or number
                raise GapError('API_ERROR', self.error(code), {'native_error': code, 'tag': tag})
            self.log('SetValue', tag, {'value': value}, elapsed=time.monotonic() - before)
        except Exception as exc:
            self.log('SetValue', tag, error=str(exc), elapsed=time.monotonic() - before)
            raise

    def close(self):
        self.server = None
        self.pythoncom.CoUninitialize()


def owned_path(value, root):
    if not isinstance(value, str) or not value or any(c in value for c in ('"', "\0", "\r", "\n")):
        raise GapError("ERROR", "Некорректный путь модели")
    file = Path(value).resolve()
    if not file.is_relative_to(root) or file.suffix.lower() != ".gap":
        raise GapError("ERROR", "Модель должна находиться внутри проекта и иметь расширение .gap")
    return file


def snapshot(client):
    count = int(float(client.get("GAP.MOD[0].EQUIP.COUNT")))
    if not 0 <= count <= 100000:
        raise GapError("API_ERROR", "Некорректное число объектов GAP")
    equipment = []
    for index in range(count):
        prefix = f"GAP.MOD[0].EQUIP[{index}]"
        item = {"index": index, "label": client.get(prefix+".LABEL"), "type": client.get(prefix+".TYPE"),
                "unique_id": client.get(prefix+".UniqueID")}
        if item["type"] == "PIPE":
            item["from_node"] = client.get(prefix+".ENDA.Label")
            item["to_node"] = client.get(prefix+".ENDB.Label")
            item["empty_pipe"] = client.get(prefix+".EmptyPipe")
        equipment.append(item)
    return {"equipment": equipment, "count": count, "model_filename": client.get("GAP.MOD[0].FILENAME"),
            "is_dirty": client.get("GAP.MOD[0].ISDIRTY"), "captured_at": datetime.now(timezone.utc).isoformat()}


def hide_owned_windows(pids):
    try:
        import win32gui
        import win32process
        def hide(window, _):
            if win32process.GetWindowThreadProcessId(window)[1] in pids:
                win32gui.ShowWindow(window, 0)
        win32gui.EnumWindows(hide, None)
    except Exception:
        pass


def start_owned(client):
    if running_gap_pids():
        raise GapError("SESSION_BUSY", "GAP уже запущен. Новая модель не создаётся поверх чужой сессии.")
    client.cmd("GAP.START()")
    pids = running_gap_pids()
    hide_owned_windows(pids)
    return pids


def ensure_smoke(client):
    state = snapshot(client)
    existing = {item["label"]: item for item in state["equipment"]}
    for label in JOINTS:
        if label in existing:
            if existing[label]["type"] != "JOINT":
                raise GapError("API_ERROR", "Коллизия типа существующего объекта")
        else:
            client.cmd(f'GAP.NEWITEM("JOINT","{label}")')
    state = snapshot(client)
    existing = {item["label"]: item for item in state["equipment"]}
    if PIPE not in existing:
        client.cmd(f'GAP.LINKITEMS(MOD[0].JOINT[{{{JOINTS[0]}}}],MOD[0].JOINT[{{{JOINTS[1]}}}],"{PIPE}")')
    else:
        pipe = existing[PIPE]
        if pipe["type"] != "PIPE" or {pipe.get("from_node"), pipe.get("to_node")} != set(JOINTS):
            raise GapError("API_ERROR", "Существующая труба имеет неверные концы")


def run(request):
    root = Path(request["workspace"]).resolve()
    job, trace_id = request["job"], request["trace_id"]
    operation = job.get("operation")
    if operation == 'build':
        from .compiler import MappingError, compile_manifest
        from .production import verify_runtime_profile
        try:
            compile_manifest(job.get('manifest'), job.get('mapping'))
            verify_runtime_profile(job['mapping'], root)
        except (MappingError, ValueError, TypeError, KeyError, OSError) as exc:
            raise GapError('UNSUPPORTED_MAPPING', str(exc)) from None
    if operation not in ("probe", "smoke", "export", "build"):
        raise GapError("ERROR", "Операция не поддерживается")
    client = Client(request["journal_path"], trace_id)
    owned = []
    unknown_state = False
    try:
        if operation == "probe":
            pids = running_gap_pids()
            result = {"execution_status": "SUCCESS", "message": "OpenServer COM доступен", "com_progid": PROGID,
                      "gap_running": bool(pids), "gap_process_ids": pids}
            if pids:
                result["snapshot"] = snapshot(client)
            return result
        target = owned_path(job.get("model_path"), root)
        target.parent.mkdir(parents=True, exist_ok=True)
        if operation in ('smoke', 'build') and target.exists():
            raise GapError("ERROR", "Тест не перезаписывает существующий файл; подготовьте новый план")
        if operation == "export" and not target.is_file():
            raise GapError("ERROR", "Сохранённая модель не найдена")
        pids = running_gap_pids()
        if pids:
            current = client.get("GAP.MOD[0].FILENAME")
            dirty = client.get("GAP.MOD[0].ISDIRTY")
            if operation != "export" or Path(current).resolve() != target or dirty != "0":
                raise GapError("SESSION_BUSY", "Открыта существующая сессия GAP. Переключение модели запрещено.", {"is_dirty": dirty})
        else:
            owned = start_owned(client)
        quoted = target.as_posix()
        if operation == 'build':
            from .compiler import MappingError
            from .production import execute_build
            try:
                return execute_build(client, job, root, target, snapshot, SOFTWARE_VERSION)
            except MappingError as exc:
                raise GapError('API_ERROR', str(exc)) from None
        if operation == "export":
            if owned:
                client.cmd(f'GAP.OPENFILE("{quoted}")')
            state = snapshot(client)
            model = {"path": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "evidence": "real", "software_version": SOFTWARE_VERSION,
                     'exported_at': datetime.now(timezone.utc).isoformat()}
            result = {"execution_status": "SUCCESS", "message": "Объекты считаны из сохранённой модели GAP", "snapshot": state, 'model': model}
            sidecar_path = target.with_suffix('.mapping.json')
            if sidecar_path.is_file():
                from .production import enriched_snapshot, verify_runtime_profile
                from .compiler import normalize_snapshot
                sidecar = json.loads(sidecar_path.read_text(encoding='utf-8'))
                verify_runtime_profile(sidecar['mapping'], root)
                result['snapshot'] = enriched_snapshot(client, sidecar['mapping'], snapshot)
                result['normalized_export'] = normalize_snapshot(result['snapshot'], model, sidecar['case_id'])
            return result
        client.cmd("GAP.NEWFILE()")
        ensure_smoke(client)
        client.cmd(f'GAP.SAVEFILE("{quoted}")')
        client.cmd(f'GAP.OPENFILE("{quoted}")')
        first = snapshot(client)
        ensure_smoke(client)
        second = snapshot(client)
        expected = {"AG_J1": "JOINT", "AG_J2": "JOINT", "AG_P1": "PIPE"}
        labels = {item["label"]: item["type"] for item in second["equipment"]}
        pipe = next((item for item in second["equipment"] if item["label"] == PIPE), {})
        checks = {"saved_file_exists": target.is_file(), "exact_equipment": labels == expected and len(second["equipment"]) == 3,
                  "pipe_endpoints": {pipe.get("from_node"), pipe.get("to_node")} == set(JOINTS),
                  "repeat_no_duplicates": [(x["label"],x["unique_id"]) for x in first["equipment"]] == [(x["label"],x["unique_id"]) for x in second["equipment"]]}
        if not all(checks.values()):
            raise GapError("API_ERROR", "Проверочный сценарий не прошёл", {"checks": checks, "snapshot": second})
        return {"execution_status": "SUCCESS", "message": "Реальный GAP: 2 узла, 1 труба, сохранение, открытие и повтор без дубликатов проверены",
                "checks": checks, "snapshot": second, "snapshot_after_reopen": first,
                "model": {"path": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                          "evidence": "real", "software_version": SOFTWARE_VERSION},
                "limitations": ["Искусственный технический пример; не утверждённый производственный фрагмент", "Гидравлический расчёт не выполнялся", "Геометрия труб и представление клапанов ещё не проверены"]}
    except GapError as exc:
        unknown_state = exc.status == "TIMEOUT"
        raise
    finally:
        if owned and not unknown_state:
            try:
                client.cmd("GAP.SHUTDOWN", timeout=10)
            except Exception as exc:
                client.log("cleanup", "owned GAP session", error=str(exc))
        client.close()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        result = run(request)
    except GapError as exc:
        result = {"execution_status": exc.status, "message": str(exc), "details": exc.details}
    except Exception as exc:
        result = {"execution_status": "ERROR", "message": f"{type(exc).__name__}: {exc}"}
    result.update(trace_id=request["trace_id"], journal_path=request["journal_path"])
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
