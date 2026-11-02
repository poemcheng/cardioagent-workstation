"""Authenticated competition workstation with an isolated local data directory."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def port_number(value):
    try:
        port = int(value)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("Port must be an integer between 1024 and 65535.")
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port must be between 1024 and 65535.")
    return port


def demo_directory(value):
    directory = Path(value).expanduser()
    if not directory.is_absolute():
        directory = ROOT / directory
    directory = directory.resolve()
    if directory in {ROOT, (ROOT / "data").resolve()}:
        raise argparse.ArgumentTypeError("Use a separate demo directory, for example integrated_demo_data.")
    if directory.exists() and not directory.is_dir():
        raise argparse.ArgumentTypeError("The demo data path is a file, not a directory.")
    return directory


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=port_number, default=8765)
    parser.add_argument("--data-dir", type=demo_directory, default=ROOT / "integrated_demo_data")
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--workspace", choices=("analysis", "aipc"), default="analysis")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    # Revalidate the Path default as argparse only converts string defaults.
    args.data_dir = demo_directory(args.data_dir)
    try:
        from waitress import create_server
        from cardio.app import create_app
    except ImportError as exc:
        print(f"Missing dependency: {exc}. Run start_demo_windows.cmd or install requirements.txt.", file=sys.stderr)
        return 2
    app = None
    server = None
    try:
        args.data_dir.mkdir(parents=True, exist_ok=True)
        app = create_app(data_dir=args.data_dir)
        server = create_server(app, host="127.0.0.1", port=args.port, threads=4,
                               max_request_body_size=48 * 1024 * 1024)
        url = f"http://127.0.0.1:{args.port}"
        print(f"CardioAgent integrated analysis: {url}", flush=True)
        print(f"Isolated demo data: {args.data_dir}", flush=True)
        print("First launch: create your own local account on the login screen. No default password.", flush=True)
        print("Included CT fixtures are synthetic. Supplied MRI labels are research references, not model predictions.", flush=True)
        print("Model weights/drivers are not bundled; AMD execution and clinical use are not validated.", flush=True)
        print("Stop with Ctrl+C. Existing work is retained in the demo directory.", flush=True)
        if args.open_browser:
            import webbrowser
            try:
                if not webbrowser.open(url + "/" + args.workspace):
                    print(f"Open the browser manually: {url}", flush=True)
            except Exception:
                print(f"Open the browser manually: {url}", flush=True)
        server.run()
    except KeyboardInterrupt:
        print("\nDemo stopped.", flush=True)
    except (OSError, ValueError) as exc:
        print(f"Cannot start demo: {exc}. Check the data directory and whether port {args.port} is in use.", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            server.close()
        if app is not None:
            app.pool.shutdown(wait=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
