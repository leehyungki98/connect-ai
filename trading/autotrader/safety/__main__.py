"""킬스위치 수동 조작 CLI.

사용법:
  python -m autotrader.safety status
  python -m autotrader.safety engage "이유"
  python -m autotrader.safety reset
"""
import argparse
import sys

from autotrader.config import KILLSWITCH_FILE
from autotrader.safety.killswitch import KillSwitch


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="autotrader.safety")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    eng = sub.add_parser("engage")
    eng.add_argument("reason")
    sub.add_parser("reset")
    args = p.parse_args(argv)

    ks = KillSwitch(KILLSWITCH_FILE)
    if args.cmd == "status":
        s = ks.state()
        print(f"engaged={s.engaged} reason={s.reason!r} at={s.engaged_at}")
    elif args.cmd == "engage":
        ks.engage(args.reason)
        print(f"KILLSWITCH ENGAGED: {args.reason}")
    elif args.cmd == "reset":
        ks.reset()
        print("killswitch reset (disengaged)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
