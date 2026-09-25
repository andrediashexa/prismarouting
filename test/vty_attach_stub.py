"""A VTY for test_vty_broker.py: the real vtyattach module, a candidate of
its own, and the four words the test types. Not a harness (no test_ name)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src", "prismash"))
import vtyattach                      # noqa: E402
args = sys.argv[1:]
chan = vtyattach.attach(args[args.index("--attach") + 1])
candidate = []
print(f"STUB-VTY pid {os.getpid()}")
while True:
    try:
        line = input(f"stub# ({len(candidate)}) ")
    except KeyboardInterrupt:
        print("^C"); continue
    except EOFError:
        break
    if line.startswith("set "):
        candidate.append(line[4:])
    elif line == "show":
        print("CANDIDATE:", " | ".join(candidate))
    elif line == "size":
        print("SIZE", os.get_terminal_size(0))
    elif line == "start shell":
        how, status = chan.request_shell()
        print(f"BACK {how} {status}")
    elif line == "die":
        os._exit(7)
    elif line == "exit":
        break
