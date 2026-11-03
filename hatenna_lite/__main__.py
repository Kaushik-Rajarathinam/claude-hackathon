"""Who's That Pokémon? (Hatenna Lite).

  python3 -m hatenna_lite console [--port 8765] [--no-bot]   demo UI (starts the bot)
  python3 -m hatenna_lite bot [--until-idle SECONDS]          the always-on bot alone
"""
import argparse

from . import bot, console


def main() -> None:
    ap = argparse.ArgumentParser(prog="hatenna_lite", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("console", help="demo UI on localhost")
    c.add_argument("--port", type=int, default=8765)
    c.add_argument("--no-bot", action="store_true", help="don't start the bot automatically")
    b = sub.add_parser("bot", help="run the always-on bot")
    b.add_argument("--until-idle", type=float, default=None, help="exit after N idle seconds (tests)")
    args = ap.parse_args()
    if args.cmd == "console":
        console.main(port=args.port, autostart=not args.no_bot)
    else:
        bot.main(until_idle=args.until_idle)


if __name__ == "__main__":
    main()
