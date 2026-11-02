from multiprocessing import freeze_support

if __name__ == "__main__":
    freeze_support()
    from pa_agent.main import main
    raise SystemExit(main())
