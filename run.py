from cardio.app import create_app

if __name__ == "__main__":
    import argparse
    from waitress import create_server
    parser = argparse.ArgumentParser()
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    server = create_server(create_app(), host="127.0.0.1", port=8765, threads=4, max_request_body_size=48*1024*1024)
    print("CardioAgent: http://127.0.0.1:8765 | local research workstation", flush=True)
    if args.open_browser:
        import webbrowser
        webbrowser.open("http://127.0.0.1:8765")
    server.run()
