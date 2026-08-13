import sys


def _utf8_stdio():
    """Piped/redirected stdio on Windows defaults to cp1252, which cannot
    encode the CLI's unicode glyphs (banner art, spinners, check marks) and
    kills unattended runs with UnicodeEncodeError. Force UTF-8 with graceful
    replacement so no cosmetic character can break CI."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main_init():
    _utf8_stdio()
    from ethernity_cloud_sdk_py.commands.init import main

    main()


def main_build():
    _utf8_stdio()
    from ethernity_cloud_sdk_py.commands.build import main

    main()


def main_publish():
    _utf8_stdio()
    from ethernity_cloud_sdk_py.commands.publish import main

    main()


def main_test():
    _utf8_stdio()
    from ethernity_cloud_sdk_py.commands.test import main

    sys.exit(main())



def main_info():
    _utf8_stdio()
    from ethernity_cloud_sdk_py.commands.info import main

    sys.exit(main())
