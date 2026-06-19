def test_package_import():
    import task_orchestrator_core

    assert task_orchestrator_core.__version__ == "0.6.0"
