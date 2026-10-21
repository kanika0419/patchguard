import sys, json, importlib.util, inspect, itertools

EDGE = [0, -1, 1, 9, 10, 11, 100, None, ""]


def main(folder, relfile, func):
    sys.path.insert(0, folder)
    spec = importlib.util.spec_from_file_location("target", f"{folder}/{relfile}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, func)
    n = len(inspect.signature(fn).parameters)
    out = {}
    for args in itertools.islice(itertools.product(EDGE, repeat=n), 60):
        try:
            out[repr(args)] = repr(fn(*args))
        except Exception as e:
            out[repr(args)] = type(e).__name__
    print(json.dumps(out))


if __name__ == "__main__":
    main(*sys.argv[1:4])