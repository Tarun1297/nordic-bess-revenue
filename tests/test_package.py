# Smoke test so pytest has something to collect (exit code 5 on an empty suite would fail CI).
import nordic_bess


def test_package_imports() -> None:
    assert nordic_bess.__doc__
