import sys

if __name__ == "__main__":
    if "--akari-worker" in sys.argv:
        sys.argv.remove("--akari-worker")
        from litechat.server import main
        sys.exit(main(restart_supported=True))
    from litechat.lifecycle import supervise
    sys.exit(supervise())
