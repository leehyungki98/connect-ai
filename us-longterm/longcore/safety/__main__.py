"""킬스위치 CLI — python -m longcore.safety status|engage "이유"|reset"""
import sys

from . import killswitch


def main(argv: list) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        pass
    elif cmd == "engage":
        if len(argv) < 2 or not argv[1].strip():
            print("사용법: python -m longcore.safety engage \"이유\"")
            return 2
        killswitch.engage(argv[1])
    elif cmd == "reset":
        killswitch.reset()
    else:
        print("사용법: python -m longcore.safety status|engage \"이유\"|reset")
        return 2
    st = killswitch.status()
    state = "차단(engaged)" if st["engaged"] else "정상(해제)"
    print(f"킬스위치: {state}")
    if st["reason"]:
        print(f"사유: {st['reason']}")
    if st["ts"]:
        print(f"시각: {st['ts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
