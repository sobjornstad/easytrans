"""Entry point for EasyTrans."""

from easytrans.app import EasyTransApp


def main() -> None:
    app = EasyTransApp()
    app.run()


if __name__ == "__main__":
    main()
