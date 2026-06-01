"""Package entry point for ``python -m gvm``.

The launchers GVM creates (the Linux ``.desktop`` file, the macOS ``.app``
bundle, and ``GVM_GUI.bat``) all invoke ``python -m gvm ...``. Python looks for
this ``__main__`` module to run a package that way, so without it every
generated launcher would fail with "No module named gvm.__main__". It simply
delegates to the CLI's :func:`gvm.main.main`.
"""

from gvm.main import main

if __name__ == "__main__":
    main()
