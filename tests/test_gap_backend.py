from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gap_integration.backend import run_job
from gap_integration.worker import Client, GapError, ensure_smoke, owned_path


class DummyClient:
    def __init__(self):
        self.items = []
        self.commands = []

    def get(self, tag):
        if tag == "GAP.MOD[0].EQUIP.COUNT":
            return str(len(self.items))
        if tag.endswith(".FILENAME"):
            return "test.gap"
        if tag.endswith(".ISDIRTY"):
            return "0"
        index = int(tag.split("EQUIP[")[1].split("]")[0])
        field = tag.split("EQUIP[",1)[1].split("].",1)[1]
        return self.items[index][field]

    def cmd(self, command):
        self.commands.append(command)
        if command.startswith("GAP.NEWITEM"):
            label = command.split('"')[3]
            self.items.append({"LABEL":label,"TYPE":"JOINT","UniqueID":str(len(self.items)+1)})
        elif command.startswith("GAP.LINKITEMS"):
            self.items.append({"LABEL":"AG_P1","TYPE":"PIPE","UniqueID":"3","ENDA.Label":"AG_J1","ENDB.Label":"AG_J2","EmptyPipe":"1"})
        else:
            raise AssertionError(command)


class BackendTests(unittest.TestCase):
    def test_smoke_repeat_does_not_duplicate_implicit_pipe(self):
        client = DummyClient()
        ensure_smoke(client)
        self.assertEqual(len(client.items),3)
        self.assertEqual(sum(command.startswith("GAP.LINKITEMS") for command in client.commands),1)
        self.assertFalse(any('NEWITEM("PIPE"' in command for command in client.commands))
        ensure_smoke(client)
        self.assertEqual(len(client.commands),3)

    def test_type_collision_is_not_silently_reused(self):
        client = DummyClient()
        client.items = [{"LABEL":"AG_J1","TYPE":"SEP","UniqueID":"9"}]
        with self.assertRaises(GapError):
            ensure_smoke(client)

    def test_paths_do_not_escape_project_or_inject_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.assertEqual(owned_path(str(root/'own.gap'),root),root/'own.gap')
            for value in (str(root.parent/'outside.gap'),str(root/'bad.txt'),str(root/'a".gap'),str(root/'a\n.gap')):
                with self.subTest(value=value), self.assertRaises(GapError):
                    owned_path(value,root)

    def test_arbitrary_operation_never_starts_worker(self):
        with tempfile.TemporaryDirectory() as directory, patch('gap_integration.backend.subprocess.run') as run:
            result=run_job({"operation":"execute_raw","command":"anything"},directory)
            self.assertEqual(result['execution_status'],'ERROR')
            run.assert_not_called()

    def test_timeout_does_not_shutdown_gap_or_claim_cancellation(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory, patch('gap_integration.backend.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',1)):
            result=run_job({"operation":"probe"},directory,timeout_seconds=1)
            self.assertEqual(result['execution_status'],'TIMEOUT')
            self.assertIn('не остановлен',result['message'])

    def test_observed_openserver_licence_failure_is_not_mislabelled_busy(self):
        class Server:
            def DoCommandAsync(self,command): return 22
            def GetErrorDescription(self,code): return 'No OpenServer license available'
        client=object.__new__(Client)
        client.server=Server()
        client.log=lambda *args,**kwargs: None
        with self.assertRaises(GapError) as caught:
            client.cmd('GAP.START()')
        self.assertEqual(caught.exception.status,'LICENSE_UNAVAILABLE')
        self.assertFalse(caught.exception.details['model_write_started'])

    def test_async_start_licence_failure_preserves_known_no_write_status(self):
        class Server:
            def DoCommandAsync(self, command): return 0
            def IsBusy(self, app): return 0
            def GetLastError(self, app): return 22
            def GetErrorDescription(self, code): return 'No OpenServer license available'
        client = object.__new__(Client)
        client.server = Server()
        client.log = lambda *args, **kwargs: None
        with self.assertRaises(GapError) as caught:
            client.cmd('GAP.START()')
        self.assertEqual(caught.exception.status, 'LICENSE_UNAVAILABLE')
        self.assertFalse(caught.exception.details['model_write_started'])

    def test_licence_error_after_model_command_does_not_claim_no_write(self):
        class Server:
            def DoCommandAsync(self, command): return 22
            def GetErrorDescription(self, code): return 'No OpenServer license available'
        client = object.__new__(Client)
        client.server = Server()
        client.log = lambda *args, **kwargs: None
        with self.assertRaises(GapError) as caught:
            client.cmd('GAP.SAVEFILE("test.gap")')
        self.assertEqual(caught.exception.status, 'API_ERROR')
        self.assertIsNone(caught.exception.details['model_write_started'])


if __name__=='__main__':
    unittest.main()
