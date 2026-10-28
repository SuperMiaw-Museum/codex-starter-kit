"""Offline launcher checks: python3 -m unittest discover -s tests -v."""
import errno
import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
import unittest


class RemoteControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bootstrap test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "checkout with spaces" / "bootstrap"
        self.bundle.mkdir(parents=True)
        source = Path(__file__).resolve().parents[1] / "bootstrap"
        for name in ("remote-control.sh", ".common-env.sh", "PROMPT.md"):
            shutil.copy2(source / name, self.bundle / name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for command in ("bash", "dirname"):
            (self.bin / command).symlink_to(shutil.which(command))
        self.npx = self.bin / "npx"
        self.npx.write_text(
            '#!/usr/bin/env bash\nprintf "NPX_STARTED\\n" >&2\n'
            'printf "%s\\0" "$@"\nexit 23\n'
        )
        self.npx.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin), HOME=str(self.root / "other home"),
                        NVM_DIR=str(self.root / "no nvm"), TERM="xterm", NO_COLOR="")
        self.command = [str(self.bundle / "remote-control.sh"), "two words", "", "--flag"]

    def run_launcher(self, terminal=False):
        if not terminal:
            return subprocess.run(self.command, env=self.env, cwd="/", capture_output=True)
        master, slave = pty.openpty()
        process = subprocess.Popen(self.command, env=self.env, cwd="/",
                                   stdout=subprocess.PIPE, stderr=slave)
        os.close(slave)
        stdout, _ = process.communicate(timeout=10)
        stderr = b""
        try:
            while True:
                try:
                    chunk = os.read(master, 4096)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break
                if not chunk:
                    break
                stderr += chunk
        finally:
            os.close(master)
        return subprocess.CompletedProcess(self.command, process.returncode, stdout, stderr)

    def check_banner(self, result):
        output = result.stderr.decode()
        self.assertIn(f'Read "{self.bundle}/PROMPT.md" via Remote Desktop Commander', output)
        self.assertIn("operational guidance for this session, acknowledge, and wait for my next request.", output)
        self.assertIn("not sent automatically", output)
        self.assertNotIn("# Remote work instructions for ChatGPT", output)
        self.assertLess(output.index("COPY INTO CHATGPT"), output.index("NPX_STARTED"))
        self.assertEqual(result.returncode, 23)
        self.assertEqual(result.stdout.split(b"\0"), [
            b"--yes", b"@wonderwhy-er/desktop-commander@latest", b"remote",
            b"two words", b"", b"--flag", b"",
        ])

    def test_redirected_banner_and_launch(self):
        result = self.run_launcher()
        self.check_banner(result)
        self.assertNotIn(b"\x1b", result.stderr)

    def test_terminal_color(self):
        result = self.run_launcher(terminal=True)
        self.check_banner(result)
        self.assertIn(b"\x1b[1;36m", result.stderr)
        self.assertIn(b"\x1b[0m", result.stderr)

    def test_no_color_and_dumb_terminal(self):
        for setting in ({"NO_COLOR": "1"}, {"TERM": "dumb"}):
            with self.subTest(setting=setting):
                self.env.update(NO_COLOR="", TERM="xterm")
                self.env.update(setting)
                result = self.run_launcher(terminal=True)
                self.check_banner(result)
                self.assertNotIn(b"\x1b", result.stderr)

    def test_missing_npx(self):
        self.npx.unlink()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 127)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"npx not found; install Node.js (or configure NVM).", result.stderr)


if __name__ == "__main__":
    unittest.main()
