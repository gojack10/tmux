#!/usr/bin/env -S uv run --no-project python
"""Real-PTY paste/extended-key checks. Run with uv run python, no dependencies.

uv run --no-project python regress/paste-keys.py --tmux ./tmux --nvim nvim
uv run --no-project python regress/paste-keys.py --tmux ./tmux --nvim nvim --user-config
uv run --no-project --with pyte python regress/paste-keys.py --tmux ./tmux --session-ui
Each case owns a fresh tmux socket and reaps its attached client in finally.
"""
import argparse
import fcntl
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tty


def receive(path):
    tty.setraw(0)
    os.write(1, b"\x1b[?2004h\x1b[>4;2m")
    Path(str(path) + ".ready").touch()
    data = bytearray(os.read(0, 65536))
    while select.select([0], [], [], 0.3)[0]:
        data.extend(os.read(0, 65536))
    path.write_bytes(data)
    time.sleep(0.1)


if len(sys.argv) == 3 and sys.argv[1] == "--receive":
    receive(Path(sys.argv[2]))
    raise SystemExit


class Terminal:
    def __init__(self, tmux, directory, command, fmt="csi-u", keys="on"):
        self.master = None
        self.pid = None
        self.stream = None
        self.base = [tmux, "-S", str(directory / "socket"), "-f", str(directory / "config")]
        (directory / "config").write_text(
            f"set -s extended-keys {keys}\nset -s extended-keys-format {fmt}\n"
            "set -s escape-time 0\nset -g status off\n"
        )
        self.env = dict(os.environ, TERM="xterm-256color")
        self.env.pop("TMUX", None)
        self.env.pop("TMUX_PANE", None)
        self.run("new-session", "-d", "-s", "test", "exec " + shlex.join(command))
        self.pid, self.master = pty.fork()
        if self.pid == 0:
            os.execvpe(tmux, self.base + ["attach-session", "-t", "test"], self.env)
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))

    def run(self, *args):
        return subprocess.run(self.base + list(args), env=self.env,
                              check=True, capture_output=True).stdout

    def drain(self, seconds):
        data = bytearray()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if select.select([self.master], [], [], max(0, end - time.monotonic()))[0]:
                try:
                    part = os.read(self.master, 65536)
                except OSError:
                    break
                if not part:
                    break
                data.extend(part)
                if self.stream is not None:
                    self.stream.feed(part.decode("utf-8", errors="replace"))
        return bytes(data)

    def send(self, data):
        while data:
            written = os.write(self.master, data)
            data = data[written:]
            self.drain(0.001)

    def wait_file(self, path):
        deadline = time.monotonic() + 8
        while not path.exists():
            assert time.monotonic() < deadline, f"no result: {path}"
            self.drain(0.02)

    def close(self):
        subprocess.run(self.base + ["kill-server"], env=self.env, capture_output=True)
        if self.master is not None:
            os.close(self.master)
        if self.pid:
            waited, _ = os.waitpid(self.pid, os.WNOHANG)
            if not waited:
                try:
                    os.kill(self.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                os.waitpid(self.pid, 0)


def raw_case(tmux, payload, expected, fmt="csi-u", fragmented=False):
    with tempfile.TemporaryDirectory(prefix="tmux-paste-") as temp:
        root = Path(temp)
        out = root / "received"
        terminal = Terminal(tmux, root, [sys.executable, __file__, "--receive", str(out)], fmt)
        try:
            terminal.wait_file(Path(str(out) + ".ready"))
            terminal.drain(0.3)
            if fragmented:
                for part in [payload[:3], payload[3:6], payload[6:-3], payload[-3:]]:
                    terminal.send(part)
                    terminal.drain(0.005)
            else:
                terminal.send(payload)
            terminal.wait_file(out)
            actual = out.read_bytes()
            assert actual == expected, (actual[:200], expected[:200])
        finally:
            terminal.close()


def nvim_case(tmux, nvim, user_config=False):
    with tempfile.TemporaryDirectory(prefix="tmux-nvim-paste-") as temp:
        root = Path(temp)
        out = root / "buffer"
        command = [nvim] + ([] if user_config else ["--clean"]) + ["-n", str(out)]
        if user_config:
            command += ["-c", "call writefile([$MYVIMRC], '" + str(root / "init") + "')"]
        terminal = Terminal(tmux, root, command)
        try:
            if user_config:
                terminal.wait_file(root / "init")
                assert (root / "init").read_text().strip(), "user config was not sourced"
            terminal.drain(1.2)
            payload = "first line\n\tsecond café 文\nlast line".encode()
            terminal.send(b"i\x1b[200~" + payload + b"\x1b[201~")
            terminal.drain(0.15)
            terminal.send(b"\x1b")
            terminal.drain(0.1)
            terminal.send(b":wq\r")
            terminal.wait_file(out)
            actual = out.read_bytes()
            assert actual == payload + b"\n", actual
        finally:
            terminal.close()


def copy_mode_case(tmux):
    with tempfile.TemporaryDirectory(prefix="tmux-copy-mode-") as temp:
        root = Path(temp)
        terminal = Terminal(tmux, root, ["sh", "-c", "printf 'top\\n'; sleep 60"])
        try:
            terminal.drain(0.2)
            for width, height in [(100, 30), (10, 5), (160, 60), (80, 24)]:
                terminal.run("resize-window", "-x", str(width), "-y", str(height))
                for _ in range(15):
                    terminal.run("copy-mode")
                    terminal.run("send-keys", "-X", "history-top")
                    terminal.run("send-keys", "-X", "cancel")
                    terminal.run("choose-tree", "-s")
                    terminal.run("send-keys", "q")
            assert terminal.run("display-message", "-p", "#{pane_in_mode}").strip() == b"0"
        finally:
            terminal.close()


def session_ui_case(tmux):
    import pyte  # Only the optional rendered-UI check needs a terminal emulator.

    with tempfile.TemporaryDirectory(prefix="tmux-session-ui-") as temp:
        root = Path(temp)
        command = [sys.executable, "-c",
                   "import time; print('\\n'.join('output-%02d' % i for i in range(50)), flush=True); time.sleep(60)"]
        terminal = Terminal(tmux, root, command)
        screen_state = pyte.Screen(100, 30)
        # This screen captures output only; pyte lacks private DSR support.
        screen_state.report_device_status = lambda *args, **kwargs: None
        terminal.stream = pyte.Stream(screen_state)
        try:
            terminal.drain(0.3)
            terminal.run("rename-session", "-t", "test", "zeta")
            for name in ["alpha", "middle"] + [f"session-{i}" for i in range(3, 12)]:
                terminal.run("new-session", "-d", "-s", name, "exec sleep 60")
            terminal.run("set-option", "-g", "tree-mode-selection-style", "fg=black,bg=yellow")
            terminal.run("choose-tree", "-s", "-t", "zeta", "-F", "#[fg=green]MARK %s")
            terminal.drain(0.2)
            screen = "\n".join(screen_state.display)
            assert "(0)" in screen and "zeta" in screen, screen
            assert "output-49" in screen, screen
            assert "MARK %s" not in screen and "MARK " in screen, screen
            lines = screen.splitlines()
            selected = next(i for i, line in enumerate(lines) if "zeta" in line and "MARK " in line)
            cell = screen_state.buffer[selected][lines[selected].index("MARK ")]
            assert cell.fg == "black" and cell.bg in ("brown", "yellow", "ffff00"), cell
            marked = [line.index(":") for line in lines if "MARK " in line]
            assert len(set(marked)) == 1, marked
            terminal.run("send-keys", "-t", "zeta", "Right")
            terminal.drain(0.05)
            screen = "\n".join(screen_state.display)
            alpha = next(line for line in screen.splitlines() if "alpha" in line and "MARK " in line)
            assert "(1)" in alpha, screen
            terminal.run("send-keys", "-t", "zeta", "Left")
            terminal.run("send-keys", "-t", "zeta", "M-a")
            terminal.drain(0.1)
            assert terminal.run("list-clients", "-F", "#{session_name}").strip() == b"session-10"
            terminal.run("move-session", "-b", "-s", "middle", "-t", "zeta")
            assert terminal.run("list-sessions", "-F", "#{session_name}").splitlines()[:3] == [b"middle", b"zeta", b"alpha"]
            terminal.run("choose-tree", "-s", "-t", "session-10")
            terminal.run("send-keys", "-t", "session-10", "g", "0")
            terminal.drain(0.1)
            assert terminal.run("list-clients", "-F", "#{session_name}").strip() == b"middle"
        finally:
            terminal.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tmux", required=True)
    parser.add_argument("--nvim")
    parser.add_argument("--user-config", action="store_true", help="load normal Neovim config instead of --clean")
    parser.add_argument("--session-ui", action="store_true")
    args = parser.parse_args()
    tmux = str(Path(shutil.which(args.tmux) or args.tmux).resolve())
    version = subprocess.check_output([tmux, "-V"], text=True).strip()
    for fmt in ["csi-u", "xterm"]:
        content = "first\n\tsecond\r\nUnicode: café 文\n".encode() + b"literal CSI-u: \x1b[106;5u"
        paste = b"\x1b[200~" + content + b"\x1b[201~"
        raw_case(tmux, paste, paste, fmt)
        print(f"PASS {version} {fmt}: paste preserves LF/CRLF/tab/UTF-8/literal escape", flush=True)
        raw_case(tmux, paste, paste, fmt, fragmented=True)
        print(f"PASS {version} {fmt}: fragmented paste markers", flush=True)
        keys = b"\x1b[105;5u\t\x1b[106;5u\r\x1b[65;6u"
        expected = keys if fmt == "csi-u" else b"\x1b[27;5;105~\t\x1b[27;5;106~\r\x1b[27;6;65~"
        raw_case(tmux, keys, expected, fmt)
        print(f"PASS {version} {fmt}: Ctrl-I != Tab, Ctrl-J != Enter, Ctrl-Shift-A", flush=True)
    if args.nvim:
        nvim_case(tmux, str(Path(shutil.which(args.nvim) or args.nvim).resolve()), args.user_config)
        config = "user-config" if args.user_config else "--clean"
        print(f"PASS {version}: real nvim {config} multiline paste", flush=True)
    if args.session_ui:
        session_ui_case(tmux)
        print(f"PASS {version}: preview bottom, legible selection, aligned rows, time format, stable expanded jump keys, Meta-a and reordered selection", flush=True)
    copy_mode_case(tmux)
    print(f"PASS {version}: 60 copy-mode/choose-tree cycles across four sizes", flush=True)


if __name__ == "__main__":
    main()
