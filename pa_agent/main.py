"""Local web server entry point."""
def main():
    import os
    import uvicorn
    uvicorn.run("app:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))

if __name__ == "__main__":
    main()
