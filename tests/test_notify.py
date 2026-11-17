from focuslog import notify


def which_everything(name):
    return "/usr/bin/" + name


def test_linux_uses_notify_send():
    argv, env = notify.build_command("FocusLog", "Round 1 done", "linux", which_everything)
    assert argv == ["notify-send", "--app-name=FocusLog", "FocusLog", "Round 1 done"]
    assert env == {}


def test_mac_passes_text_as_arguments_not_script():
    tricky = 'Math" & do shell script "rm -rf ~'
    argv, _ = notify.build_command("FocusLog", tricky, "darwin", which_everything)
    assert argv[0] == "osascript"
    assert argv[-1] == tricky                      # separate argument
    assert all(tricky not in part for part in argv[:-1])  # never inside the script


def test_windows_passes_text_through_environment():
    tricky = "Math'; Remove-Item C:\\ -Recurse"
    argv, env = notify.build_command("FocusLog", tricky, "win32", which_everything)
    assert argv[0] == "powershell"
    assert tricky not in " ".join(argv)
    assert env["FOCUSLOG_MESSAGE"] == tricky


def test_no_tool_available():
    assert notify.build_command("a", "b", "linux", lambda name: None) is None


def test_notify_never_raises(monkeypatch):
    def exploding_popen(*args, **kwargs):
        raise OSError("no such program")

    monkeypatch.setattr(notify, "build_command", lambda title, message: (["x"], {}))
    assert notify.notify("a", "b", bell=False, popen=exploding_popen) is False


def test_notify_starts_the_command(monkeypatch):
    started = []
    monkeypatch.setattr(notify, "build_command", lambda title, message: (["x", title], {"K": "v"}))
    assert notify.notify("hello", "b", bell=False,
                         popen=lambda argv, **kw: started.append((argv, kw["env"]["K"]))) is True
    assert started == [(["x", "hello"], "v")]
