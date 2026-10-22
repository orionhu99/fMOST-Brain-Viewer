"""Installer contracts and opt-in Windows integration tests with a tiny fake viewer.

FMOST_TEST_INSTALLER=1 runs the real Inno script with an isolated TestInstall ID
and a temporary one-file payload. No installed viewer or user session is used.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "installer" / "fmost_brain_viewer.iss"


class InstallerSourceContractTests(unittest.TestCase):
    def test_update_launch_is_unconditional_in_silent_mode_and_preserves_user(self):
        entries = SCRIPT.read_text(encoding="utf-8").split("[Run]", 1)[1].split("[Code]", 1)[0]
        resume = next(line for line in entries.splitlines() if "GetUpdateResumeParameters" in line)
        self.assertIn("runasoriginaluser", resume)
        self.assertIn("Check: IsUpdateInstall", resume)
        self.assertNotIn("skipifsilent", resume)
        self.assertNotIn("postinstall", resume)
        ordinary = next(line for line in entries.splitlines() if "LaunchProgram" in line)
        self.assertIn("Check: IsRegularInstall", ordinary)
        self.assertIn("postinstall skipifsilent", ordinary)

    def test_update_is_per_user_and_never_deletes_user_directories(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("PrivilegesRequired=lowest", text)
        self.assertIn("AppId={{6B53B932-627A-4A3E-8B1E-EA8F15AA0A47}", text)
        self.assertNotIn("[InstallDelete]", text)
        self.assertNotIn("[UninstallDelete]", text)
        self.assertNotIn("TerminateProcess", text)
        self.assertIn("UpdateWaitMilliseconds = 60000", text)


@unittest.skipUnless(os.name == "nt" and os.environ.get("FMOST_TEST_INSTALLER") == "1",
                     "set FMOST_TEST_INSTALLER=1 on Windows to run isolated Inno integration")
class InstallerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        local = Path(os.environ["LOCALAPPDATA"])
        compiler = Path(os.environ.get("FMOST_ISCC", local / "Programs/Inno Setup 6/ISCC.exe"))
        csc = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        if not compiler.is_file() or not csc.is_file():
            raise unittest.SkipTest("Inno Setup 6 and Windows .NET Framework C# compiler required")
        cls.root = Path(tempfile.mkdtemp(prefix="fmost-update-contract-"))
        cls.root_parent = cls.root.parent.resolve()
        cls.updates = local / "fMOST Brain Viewer" / "updates"
        cls.updates.mkdir(parents=True, exist_ok=True)
        cls.payload = cls.root / "fMOST Brain Viewer.exe"
        source = cls.root / "Viewer.cs"
        source.write_text(r'''
using System;
using System.IO;
using System.Threading;
class Viewer {
    static int Main(string[] args) {
        if (args.Length == 3 && args[0] == "--hold") {
            File.WriteAllText(args[2], "ready");
            Thread.Sleep(int.Parse(args[1]));
        } else if (args.Length == 2 && args[0] == "--resume-update") {
            File.WriteAllText(args[1] + ".resumed", args[1] + "\n" + Environment.UserName);
        }
        return 0;
    }
}''', encoding="utf-8")
        subprocess.run([str(csc), "/nologo", "/target:winexe", f"/out:{cls.payload}", str(source)],
                       check=True, capture_output=True, timeout=30)
        script = SCRIPT.read_text(encoding="utf-8")
        script = re.sub(r'^Source: "\.\.\\dist\\fMOST Brain Viewer\\\*";',
                        lambda _: f'Source: "{cls.payload}";', script, flags=re.MULTILINE)
        script = script.replace("SetupIconFile=..\\assets\\fmost_brain_viewer.ico",
                                f"SetupIconFile={ROOT / 'assets/fmost_brain_viewer.ico'}")
        # Exercise the exact timeout branch without making the fixture wait a minute.
        script = script.replace("UpdateWaitMilliseconds = 60000;", "UpdateWaitMilliseconds = 1500;")
        fixture = cls.root / "installer.iss"
        fixture.write_text(script, encoding="utf-8-sig")
        subprocess.run([str(compiler), "/Q", "/DMyAppVersion=0.0.0", "/DTestInstall",
                        f"/O{cls.root}", str(fixture)], check=True, capture_output=True, timeout=60)
        cls.installer = cls.root / "fMOST-Brain-Viewer-Installer-Test-0.0.0.exe"

    @classmethod
    def tearDownClass(cls):
        if cls.root.resolve().parent != cls.root_parent or not cls.root.name.startswith("fmost-update-contract-"):
            raise RuntimeError("Unsafe fixture cleanup path")
        shutil.rmtree(cls.root)

    def setUp(self):
        self.target = self.root / ("安装测试 " + uuid.uuid4().hex)
        self.session = self.updates / ("resume-" + uuid.uuid4().hex + ".fmost-session.json")
        self.session.write_text("{}", encoding="utf-8")
        self.resumed = self.session.with_name(self.session.name + ".resumed")
        self.children = []

    def tearDown(self):
        for process in self.children:
            process.wait(timeout=10)
        self.session.unlink(missing_ok=True)
        self.resumed.unlink(missing_ok=True)

    def install(self, *arguments):
        return subprocess.run([str(self.installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/SP-",
                               "/NORESTART", "/NOFORCECLOSEAPPLICATIONS", f"/DIR={self.target}",
                               *arguments], capture_output=True, timeout=30).returncode

    def start_viewer(self, milliseconds):
        marker = self.target / "started.txt"
        child = subprocess.Popen([str(self.target / "fMOST Brain Viewer.exe"),
                                  "--hold", str(milliseconds), str(marker)])
        self.children.append(child)
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(marker.exists())
        return child

    def test_waits_for_original_viewer_and_resumes_after_silent_update(self):
        self.assertEqual(self.install(), 0)
        sentinel = self.target / "my original data.txt"
        sentinel.write_text("user data", encoding="utf-8")
        child = self.start_viewer(1000)
        self.assertEqual(self.install(f"/UPDATEPID={child.pid}", f"/UPDATESESSION={self.session}"), 0)
        self.assertIsNotNone(child.poll())
        deadline = time.monotonic() + 5
        while not self.resumed.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(self.resumed.exists(), "Silent upgrade did not relaunch the viewer")
        self.assertEqual(self.resumed.read_text().splitlines()[0], str(self.session))
        self.assertEqual(sentinel.read_text(), "user data")

    def test_running_viewer_timeout_aborts_without_terminating_it(self):
        self.assertEqual(self.install(), 0)
        child = self.start_viewer(8000)
        self.assertNotEqual(self.install(f"/UPDATEPID={child.pid}", f"/UPDATESESSION={self.session}"), 0)
        self.assertIsNone(child.poll(), "Installer terminated the running viewer")
        self.assertFalse(self.resumed.exists())

    def test_unrelated_pid_and_invalid_handoff_are_rejected_before_installation(self):
        invalid = [
            (f"/UPDATEPID={os.getpid()}", f"/UPDATESESSION={self.session}"),
            ("/UPDATEPID=0", f"/UPDATESESSION={self.session}"),
            ("/UPDATEPID=-1", f"/UPDATESESSION={self.session}"),
            ("/UPDATEPID=0x10", f"/UPDATESESSION={self.session}"),
            (f"/UPDATEPID={os.getpid()}", f"/UPDATESESSION={self.root / 'escape.json'}"),
            (f"/UPDATEPID={os.getpid()}",),
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                self.assertNotEqual(self.install(*arguments), 0)
                self.assertFalse((self.target / "fMOST Brain Viewer.exe").exists())
        self.assertFalse(self.resumed.exists())

    def test_ordinary_silent_install_does_not_start_viewer(self):
        self.assertEqual(self.install(), 0)
        self.assertTrue((self.target / "fMOST Brain Viewer.exe").exists())
        self.assertFalse(self.resumed.exists())


if __name__ == "__main__":
    unittest.main()
