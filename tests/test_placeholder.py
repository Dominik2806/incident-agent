import incident_agent


def test_package_imports() -> None:
    assert isinstance(incident_agent.__version__, str)
