def main_init():
    from ethernity_cloud_sdk_py.commands.init import main

    main()


def main_build():
    from ethernity_cloud_sdk_py.commands.build import main

    main()


def main_publish():
    from ethernity_cloud_sdk_py.commands.publish import main

    main()


def main_test():
    import sys

    from ethernity_cloud_sdk_py.commands.test import main

    sys.exit(main())
